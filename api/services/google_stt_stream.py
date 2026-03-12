from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, Optional


MAX_AUDIO_CHUNK_BYTES = 15_360


class GoogleSpeechStreamError(RuntimeError):
    """Google 语音流接入错误。"""


@dataclass(slots=True)
class GoogleSpeechStreamConfig:
    project_id: str
    location: str = "global"
    recognizer: str = "_"
    language_codes: list[str] = field(default_factory=lambda: ["cmn-Hans-CN"])
    model: str = "chirp_3"
    sample_rate_hz: int = 16000
    audio_channel_count: int = 1
    enable_automatic_punctuation: bool = True
    enable_voice_activity_events: bool = True
    speech_start_timeout_seconds: float = 5.0
    speech_end_timeout_seconds: float = 1.8
    api_endpoint: str | None = None


class GoogleSpeechStreamBridge:
    """将前端 WebSocket 音频流桥接到 Google Speech-to-Text V2。"""

    def __init__(self, config: GoogleSpeechStreamConfig) -> None:
        self.config = config
        self._audio_queue: Queue[bytes | None] = Queue()
        self._event_queue: Queue[dict[str, Any]] = Queue()
        self._abort_event = Event()
        self._input_closed = Event()
        self._thread: Optional[Thread] = None
        self._client: Any = None
        self._cloud_speech: Any = None
        self._duration_pb2: Any = None

    def start(self) -> None:
        if self._thread is not None:
            return

        if not self.config.project_id:
            raise GoogleSpeechStreamError("未配置 GOOGLE_CLOUD_PROJECT，无法启用 Google 语音识别。")
        if not self.config.language_codes:
            raise GoogleSpeechStreamError("未配置 Google 语音识别语言。")

        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud.speech_v2 import SpeechClient
            from google.cloud.speech_v2.types import cloud_speech
            from google.protobuf import duration_pb2
        except ImportError as exc:
            raise GoogleSpeechStreamError(
                "未安装 google-cloud-speech，请在 backend 中执行 `uv add google-cloud-speech` 或 `uv sync`。"
            ) from exc

        self._cloud_speech = cloud_speech
        self._duration_pb2 = duration_pb2

        api_endpoint = self.config.api_endpoint or self._resolve_api_endpoint(self.config.location)
        self._client = SpeechClient(
            client_options=ClientOptions(
                api_endpoint=api_endpoint,
                quota_project_id=self.config.project_id,
            )
        )

        self._thread = Thread(target=self._run, name="google-speech-stream", daemon=True)
        self._thread.start()

    @staticmethod
    def _resolve_api_endpoint(location: str) -> str:
        normalized = (location or "global").strip()
        if normalized == "global":
            return "speech.googleapis.com"
        return f"{normalized}-speech.googleapis.com"

    def _recognizer_path(self) -> str:
        recognizer = self.config.recognizer.strip() or "_"
        if recognizer.startswith("projects/"):
            return recognizer
        return (
            f"projects/{self.config.project_id}/locations/{self.config.location}/recognizers/{recognizer}"
        )

    def _build_streaming_config(self) -> Any:
        cloud_speech = self._cloud_speech

        recognition_config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self.config.sample_rate_hz,
                audio_channel_count=self.config.audio_channel_count,
            ),
            language_codes=self.config.language_codes,
            model=self.config.model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=self.config.enable_automatic_punctuation,
            ),
        )

        streaming_features_kwargs = {
            "interim_results": True,
            "enable_voice_activity_events": self.config.enable_voice_activity_events,
        }

        if self.config.enable_voice_activity_events:
            streaming_features_kwargs["voice_activity_timeout"] = (
                cloud_speech.StreamingRecognitionFeatures.VoiceActivityTimeout(
                    speech_start_timeout=self._duration_pb2.Duration(
                        seconds=int(self.config.speech_start_timeout_seconds),
                        nanos=int((self.config.speech_start_timeout_seconds % 1) * 1_000_000_000),
                    ),
                    speech_end_timeout=self._duration_pb2.Duration(
                        seconds=int(self.config.speech_end_timeout_seconds),
                        nanos=int((self.config.speech_end_timeout_seconds % 1) * 1_000_000_000),
                    ),
                )
            )

        return cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(**streaming_features_kwargs),
        )

    def _request_iter(self):
        cloud_speech = self._cloud_speech

        yield cloud_speech.StreamingRecognizeRequest(
            recognizer=self._recognizer_path(),
            streaming_config=self._build_streaming_config(),
        )

        while not self._abort_event.is_set():
            chunk = self._audio_queue.get()
            if chunk is None:
                break
            if not chunk:
                continue
            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    def _map_speech_event(self, event_type: Any) -> str | None:
        cloud_speech = self._cloud_speech
        event_enum = cloud_speech.StreamingRecognizeResponse.SpeechEventType
        if event_type == event_enum.SPEECH_ACTIVITY_BEGIN:
            return "begin"
        if event_type == event_enum.SPEECH_ACTIVITY_END:
            return "end"
        return None

    def _emit_transcript_events(self, response: Any) -> None:
        speech_event = self._map_speech_event(response.speech_event_type)
        if speech_event:
            self._event_queue.put({"type": "speech_event", "event": speech_event})

        for result in response.results:
            alternatives = getattr(result, "alternatives", None) or []
            transcript = alternatives[0].transcript if alternatives else ""
            if not transcript:
                continue
            self._event_queue.put(
                {
                    "type": "transcript",
                    "text": transcript,
                    "isFinal": bool(result.is_final),
                }
            )

    def _run(self) -> None:
        try:
            responses = self._client.streaming_recognize(requests=self._request_iter())
            for response in responses:
                if self._abort_event.is_set():
                    break
                self._emit_transcript_events(response)
        except Exception as exc:
            if not self._abort_event.is_set():
                self._event_queue.put(
                    {"type": "error", "message": f"Google 语音识别失败: {exc}"}
                )
        finally:
            self._event_queue.put({"type": "closed"})
            self._close_client()

    def push_audio(self, audio_chunk: bytes) -> None:
        if self._thread is None:
            raise GoogleSpeechStreamError("Google 语音流尚未启动。")
        if self._input_closed.is_set() or self._abort_event.is_set():
            return

        for idx in range(0, len(audio_chunk), MAX_AUDIO_CHUNK_BYTES):
            self._audio_queue.put(audio_chunk[idx: idx + MAX_AUDIO_CHUNK_BYTES])

    def finish_input(self) -> None:
        if self._input_closed.is_set():
            return
        self._input_closed.set()
        self._audio_queue.put(None)

    def abort(self) -> None:
        self._abort_event.set()
        self.finish_input()
        self._close_client()

    def _close_client(self) -> None:
        if self._client is None:
            return

        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self._client = None

    def next_event(self, timeout: float = 0.0) -> dict[str, Any] | None:
        try:
            return self._event_queue.get(timeout=timeout)
        except Empty:
            return None
