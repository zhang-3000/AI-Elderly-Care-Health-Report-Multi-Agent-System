#!/usr/bin/env python3
"""
FastAPI 服务器入口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.websockets import WebSocketState


code_dir = Path(__file__).parent.parent / "code"
api_dir = Path(__file__).parent
core_dir = Path(__file__).parent.parent / "core"
base_dir = Path(__file__).parent.parent
sys.path.insert(0, str(code_dir))
sys.path.insert(0, str(api_dir))
sys.path.insert(0, str(core_dir))

from auth_routes import auth_router
from auth_service import AuthService, DOCTOR_ROLE, ELDERLY_ROLE
from doctor_routes import doctor_router
from doctor_service import DoctorService
from elderly_routes import elderly_router
from family_routes import family_router
from mappers import to_backend_profile, to_frontend_report_data
from memory.conversation_manager import ConversationManager, SessionState
from memory.family_caregiver_manager import FamilyCaregiverManager
from report_utils import (
    list_reports_for_user,
    load_report_payload,
    profile_to_dict,
    resolve_report_owner,
    save_report_bundle,
)
from schemas import (
    AgentStatusEvent,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatProgressResponse,
    ChatStartResponse,
    ReportData,
    ReportGenerateByElderlyResponse,
    ReportGenerateRequest,
)
from security import (
    ensure_actor_can_access_session,
    ensure_actor_can_access_user,
    ensure_actor_can_view_session,
    require_authenticated_actor,
    require_elderly_session_access,
    require_state,
)
from workspace_manager import WorkspaceManager


logger = logging.getLogger(__name__)

try:
    from services.google_stt_stream import (
        GoogleSpeechStreamBridge,
        GoogleSpeechStreamConfig,
        GoogleSpeechStreamError,
    )

    GOOGLE_STT_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - optional dependency
    GoogleSpeechStreamBridge = None
    GoogleSpeechStreamConfig = None
    GOOGLE_STT_IMPORT_ERROR = exc

    class GoogleSpeechStreamError(RuntimeError):
        """Google STT 不可用时的占位异常。"""


def _resolve_project_path(env_name: str, default_relative_path: str) -> Path:
    raw_value = (os.getenv(env_name) or "").strip()
    if not raw_value:
        return base_dir / default_relative_path

    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate


DB_PATH = str(_resolve_project_path("DB_PATH", "data/users.db"))
REPORTS_DIR = base_dir / "data" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
RAG_ENABLED = os.getenv("RAG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

STATE_MAP = {
    SessionState.GREETING: "greeting",
    SessionState.COLLECTING: "collecting",
    SessionState.CONFIRMING: "confirming",
    SessionState.GENERATING: "generating",
    SessionState.REPORT_DONE: "completed",
    SessionState.FOLLOW_UP: "follow_up",
}


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _require_ws_state(websocket: WebSocket, attr: str, error_message: str):
    manager = getattr(websocket.app.state, attr, None)
    if manager is None:
        raise RuntimeError(error_message)
    return manager


def _state_to_api(value: Any) -> str:
    return STATE_MAP.get(value, str(value or "unknown").lower())


def _serialize_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "role": message.get("role", "user"),
            "content": message.get("content", ""),
            "timestamp": message.get("timestamp"),
        }
        for message in history
    ]


def _has_profile_content(profile: Dict[str, Any] | None) -> bool:
    if not isinstance(profile, dict):
        return False
    for key, value in profile.items():
        if key == "user_type":
            continue
        if isinstance(value, list) and any(item not in (None, "") for item in value):
            return True
        if value not in (None, "", [], {}):
            return True
    return False


def _ensure_workspace_metadata(
    workspace_manager: WorkspaceManager,
    session_id: str,
    user_id: str,
    created_at: Optional[str] = None,
) -> None:
    metadata = workspace_manager.get_session_metadata(session_id)
    base_metadata = {
        "session_id": session_id,
        "user_id": user_id,
        "created_at": created_at or metadata.get("created_at") or datetime.now().isoformat(),
        "status": metadata.get("status", "active"),
        "title": metadata.get("title", f"评估记录 - {datetime.now().strftime('%m-%d %H:%M')}"),
        "has_report": metadata.get("has_report", False),
        "has_profile": metadata.get("has_profile", False),
    }
    workspace_manager.create_metadata(session_id, base_metadata)


def _find_or_create_session_for_user(
    conversation_manager: ConversationManager,
    workspace_manager: WorkspaceManager,
    user_id: str,
) -> str:
    latest_session = conversation_manager.store.get_latest_session(user_id)
    if latest_session is None:
        session_id = conversation_manager.new_session(user_id)
        created_at = datetime.now().isoformat()
    else:
        session_id = latest_session["session_id"]
        created_at = latest_session.get("created_at")

    _ensure_workspace_metadata(workspace_manager, session_id, user_id, created_at=created_at)
    return session_id


def _persist_workspace_snapshot(
    workspace_manager: WorkspaceManager,
    session_id: str,
    user_id: str,
    profile: Dict[str, Any] | None = None,
    history: List[Dict[str, Any]] | None = None,
) -> None:
    _ensure_workspace_metadata(workspace_manager, session_id, user_id)

    if history is not None:
        workspace_manager.save_conversation(session_id, _serialize_history(history))

    if _has_profile_content(profile):
        workspace_manager.save_user_profile(session_id, profile)
        workspace_manager.update_metadata(session_id, {"has_profile": True})


def _extract_profile_updates(raw_profile: Dict[str, Any]) -> Dict[str, Any]:
    profile = to_backend_profile(raw_profile)
    return {
        key: value
        for key, value in profile_to_dict(profile).items()
        if value is not None
    }


def _save_generated_chat_report_if_needed(
    result: Dict[str, Any],
    reports_dir: Path,
    workspace_manager: WorkspaceManager,
    session_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    generated_results = result.get("generated_report_results")
    generated_profile = result.get("generated_report_profile")
    if not isinstance(generated_results, dict) or not isinstance(generated_profile, dict):
        return None

    normalized_profile = dict(generated_profile)
    normalized_profile.pop("user_type", None)
    report_data = to_frontend_report_data(generated_results)
    return save_report_bundle(
        reports_dir=reports_dir,
        workspace_manager=workspace_manager,
        profile=normalized_profile,
        results=generated_results,
        report_data=report_data,
        session_id=session_id,
        user_id=user_id,
    )


async def _run_report_workflow(
    conversation_manager: ConversationManager,
    profile,
    stage_callback=None,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        conversation_manager.orchestrator.run,
        profile,
        False,
        stage_callback,
    )


def _visible_user_ids_for_actor(request: Request, actor) -> Optional[set[str]]:
    if actor.role == DOCTOR_ROLE:
        return None
    if actor.role == ELDERLY_ROLE:
        return {actor.subject_id}
    auth_service = require_state(request, "auth_service", "认证服务未初始化")
    return set(auth_service.list_family_elderly_ids(actor.subject_id))


def _load_accessible_report_payload(request: Request, report_id: str) -> Dict[str, Any]:
    actor = require_authenticated_actor(request)
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    reports_dir = require_state(request, "reports_dir", "报告目录未初始化")
    payload = load_report_payload(report_id, reports_dir, workspace_manager)
    if payload is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    owner_id = resolve_report_owner(payload, workspace_manager)
    if owner_id is None:
        raise HTTPException(status_code=404, detail="报告归属缺失")

    visible_user_ids = _visible_user_ids_for_actor(request, actor)
    if visible_user_ids is not None and owner_id not in visible_user_ids:
        raise HTTPException(status_code=403, detail="无权访问该报告")
    return payload


def build_google_speech_config(language_code: Optional[str] = None):
    if GoogleSpeechStreamConfig is None:
        raise GoogleSpeechStreamError(
            f"Google 语音识别依赖不可用: {GOOGLE_STT_IMPORT_ERROR}"
        )

    languages = [
        item.strip()
        for item in _env_first(
            "GOOGLE_SPEECH_LANGUAGE_CODES",
            "GOOGLE_CLOUD_SPEECH_LANGUAGE_CODES",
            default="cmn-Hans-CN",
        ).split(",")
        if item.strip()
    ]
    if language_code:
        languages = [language_code]

    location = _env_first("GOOGLE_SPEECH_LOCATION", "GOOGLE_CLOUD_SPEECH_LOCATION", default="global")
    api_endpoint = _env_first("GOOGLE_SPEECH_API_ENDPOINT", "GOOGLE_CLOUD_SPEECH_API_ENDPOINT") or None
    if api_endpoint is None and location != "global":
        api_endpoint = f"{location}-speech.googleapis.com"

    return GoogleSpeechStreamConfig(
        project_id=_env_first("GOOGLE_CLOUD_PROJECT"),
        location=location,
        recognizer=_env_first("GOOGLE_SPEECH_RECOGNIZER", "GOOGLE_CLOUD_SPEECH_RECOGNIZER", default="_"),
        language_codes=languages or ["cmn-Hans-CN"],
        model=_env_first("GOOGLE_SPEECH_MODEL", "GOOGLE_CLOUD_SPEECH_MODEL", default="chirp_3"),
        sample_rate_hz=_env_int(
            "GOOGLE_SPEECH_SAMPLE_RATE_HZ",
            _env_int("GOOGLE_CLOUD_SPEECH_SAMPLE_RATE_HZ", 16000),
        ),
        audio_channel_count=_env_int(
            "GOOGLE_SPEECH_CHANNEL_COUNT",
            _env_int("GOOGLE_CLOUD_SPEECH_CHANNEL_COUNT", 1),
        ),
        enable_automatic_punctuation=_env_flag("GOOGLE_SPEECH_ENABLE_PUNCTUATION", True),
        enable_voice_activity_events=_env_flag("GOOGLE_SPEECH_ENABLE_VOICE_ACTIVITY_EVENTS", True),
        speech_start_timeout_seconds=_env_float("GOOGLE_SPEECH_START_TIMEOUT_SEC", 5.0),
        speech_end_timeout_seconds=_env_float("GOOGLE_SPEECH_END_TIMEOUT_SEC", 1.8),
        api_endpoint=api_endpoint,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    app.state.conversation_manager = ConversationManager(db_path=DB_PATH)
    app.state.family_manager = FamilyCaregiverManager(db_path=DB_PATH)
    app.state.workspace_manager = WorkspaceManager(base_dir=str(Path(__file__).parent.parent / "workspace"))
    app.state.auth_service = AuthService(db_path=DB_PATH)
    app.state.doctor_service = DoctorService(db_path=DB_PATH)
    app.state.reports_dir = REPORTS_DIR

    print("✓ FastAPI 服务器已启动")
    print(f"✓ 数据库路径: {DB_PATH}")
    print(f"✓ 工作区路径: {app.state.workspace_manager.base_dir}")

    yield

    print("FastAPI 服务器正在关闭")


app = FastAPI(
    title="AI 养老健康助手 API",
    description="提供健康评估对话和报告生成服务",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_router = APIRouter(prefix="/api")
chat_router = APIRouter(prefix="/chat")
report_router = APIRouter(prefix="/report")


@chat_router.post("/start", response_model=ChatStartResponse)
async def start_chat(request: Request) -> ChatStartResponse:
    """开始新的健康评估对话，并签发老人访问 token。"""
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    auth_service = require_state(request, "auth_service", "认证服务未初始化")

    user_id = conversation_manager.new_user()
    session_id = conversation_manager.new_session(user_id)
    result = conversation_manager.chat(session_id, "")
    history = conversation_manager.get_history(session_id)
    issued_token = auth_service.issue_elderly_token(user_id)

    _persist_workspace_snapshot(
        workspace_manager,
        session_id,
        user_id,
        profile=conversation_manager.get_profile(session_id),
        history=history,
    )

    return ChatStartResponse(
        userId=user_id,
        sessionId=session_id,
        welcomeMessage=result.get("reply", "您好！我是AI养老健康助手。"),
        accessToken=issued_token.token,
        userType=ELDERLY_ROLE,
        expiresAt=issued_token.expires_at,
    )


@chat_router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: Request, payload: ChatMessageRequest) -> ChatMessageResponse:
    """发送聊天消息。"""
    _, owner_user_id = require_elderly_session_access(request, payload.sessionId)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    reports_dir = require_state(request, "reports_dir", "报告目录未初始化")

    try:
        result = conversation_manager.chat(payload.sessionId, payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"处理消息失败: {exc}") from exc

    state = result.get("state")
    completed = state == SessionState.REPORT_DONE

    _save_generated_chat_report_if_needed(
        result=result,
        reports_dir=reports_dir,
        workspace_manager=workspace_manager,
        session_id=payload.sessionId,
        user_id=owner_user_id,
    )
    _persist_workspace_snapshot(
        workspace_manager,
        payload.sessionId,
        owner_user_id,
        profile=conversation_manager.get_profile(payload.sessionId),
        history=conversation_manager.get_history(payload.sessionId),
    )

    return ChatMessageResponse(
        message=result.get("reply", ""),
        state=_state_to_api(state),
        progress=result.get("progress", 0.0),
        completed=completed,
    )


@chat_router.get("/history/{session_id}")
async def get_chat_history(request: Request, session_id: str) -> List[Dict[str, Any]]:
    """获取会话历史。"""
    require_elderly_session_access(request, session_id)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    return _serialize_history(conversation_manager.store.get_session_history(session_id))


@chat_router.get("/progress/{session_id}", response_model=ChatProgressResponse)
async def get_chat_progress(request: Request, session_id: str) -> ChatProgressResponse:
    """获取会话当前进度。"""
    require_elderly_session_access(request, session_id)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")

    try:
        progress = conversation_manager.get_progress(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取进度失败: {exc}") from exc

    return ChatProgressResponse(
        state=_state_to_api(progress.get("state")),
        progress=progress.get("progress", 0.0),
        completedGroups=progress.get("completed_groups", []),
        pendingGroups=progress.get("pending_groups", []),
        missingFields=progress.get("missing_fields", {}),
    )


@chat_router.get("/profile/{session_id}")
async def get_chat_profile(request: Request, session_id: str) -> Dict[str, Any]:
    """获取会话当前用户画像。"""
    require_elderly_session_access(request, session_id)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")

    try:
        profile = conversation_manager.get_profile(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取画像失败: {exc}") from exc

    if profile is None:
        raise HTTPException(status_code=404, detail="未找到用户画像")
    return profile


@chat_router.get("/stream")
async def stream_chat(request: Request, message: str, sessionId: str):
    """流式聊天 (SSE)。"""
    _, owner_user_id = require_elderly_session_access(request, sessionId)

    async def event_generator() -> AsyncGenerator[str, None]:
        conversation_manager = getattr(request.app.state, "conversation_manager", None)
        workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
        reports_dir = require_state(request, "reports_dir", "报告目录未初始化")
        if conversation_manager is None:
            yield f"data: {json.dumps({'error': '对话管理器未初始化'})}\n\n"
            return

        try:
            result = conversation_manager.chat(sessionId, message)
            _save_generated_chat_report_if_needed(
                result=result,
                reports_dir=reports_dir,
                workspace_manager=workspace_manager,
                session_id=sessionId,
                user_id=owner_user_id,
            )
            _persist_workspace_snapshot(
                workspace_manager,
                sessionId,
                owner_user_id,
                profile=conversation_manager.get_profile(sessionId),
                history=conversation_manager.get_history(sessionId),
            )
            reply = result.get("reply", "")
            for char in reply:
                yield f"data: {json.dumps({'content': char})}\n\n"
                await asyncio.sleep(0.02)
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/stt")
async def stream_speech_to_text(websocket: WebSocket):
    await websocket.accept()

    bridge: Optional[Any] = None
    sender_task: Optional[asyncio.Task[None]] = None

    async def send_bridge_events() -> None:
        assert bridge is not None
        while True:
            event = await asyncio.to_thread(bridge.next_event, 0.25)
            if event is None:
                continue
            await websocket.send_json(event)
            if event.get("type") in {"closed", "error"}:
                break

    try:
        _require_ws_state(websocket, "conversation_manager", "对话管理器未初始化")

        start_payload = await websocket.receive_json()
        if start_payload.get("type") != "start":
            await websocket.send_json({"type": "error", "message": "Invalid speech start message."})
            await websocket.close(code=1003)
            return

        if GoogleSpeechStreamBridge is None:
            raise GoogleSpeechStreamError(f"Google 语音识别依赖不可用: {GOOGLE_STT_IMPORT_ERROR}")

        bridge = GoogleSpeechStreamBridge(
            build_google_speech_config(language_code=start_payload.get("lang"))
        )
        bridge.start()

        await websocket.send_json({"type": "ready", "engine": "google_stt_v2"})
        sender_task = asyncio.create_task(send_bridge_events())

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            binary_chunk = message.get("bytes")
            if binary_chunk is not None:
                bridge.push_audio(binary_chunk)
                continue

            text_payload = message.get("text")
            if not text_payload:
                continue

            try:
                payload = json.loads(text_payload)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid speech control message."})
                break

            if payload.get("type") == "stop":
                bridge.finish_input()
                break

        if bridge is not None:
            bridge.finish_input()
        if sender_task is not None:
            await sender_task
    except WebSocketDisconnect:
        if bridge is not None:
            bridge.abort()
    except GoogleSpeechStreamError as exc:
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close(code=1011)
    except Exception as exc:
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.send_json({"type": "error", "message": f"Speech stream failed: {exc}"})
            await websocket.close(code=1011)
    finally:
        if bridge is not None:
            bridge.abort()
        if sender_task is not None and not sender_task.done():
            sender_task.cancel()


@report_router.post("/generate", response_model=ReportData)
async def generate_report(request: Request, payload: ReportGenerateRequest) -> ReportData:
    """根据给定画像生成标准报告。"""
    require_authenticated_actor(request)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    reports_dir = require_state(request, "reports_dir", "报告目录未初始化")

    if not payload.sessionId:
        raise HTTPException(status_code=400, detail="生成报告必须提供 sessionId")

    _, session_owner_id = ensure_actor_can_access_session(request, payload.sessionId)

    try:
        profile = to_backend_profile(payload.profile)
        results = await _run_report_workflow(conversation_manager, profile)
        report_data = to_frontend_report_data(results)
        save_report_bundle(
            reports_dir=reports_dir,
            workspace_manager=workspace_manager,
            profile=profile_to_dict(profile),
            results=results,
            report_data=report_data,
            session_id=payload.sessionId,
            user_id=session_owner_id,
        )
        _persist_workspace_snapshot(
            workspace_manager,
            payload.sessionId,
            session_owner_id,
            profile=profile_to_dict(profile),
        )
        return ReportData(**report_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {exc}") from exc


@report_router.post("/stream")
async def stream_report(request: Request, payload: ReportGenerateRequest):
    """流式生成报告 (SSE)。"""
    require_authenticated_actor(request)
    if not payload.sessionId:
        raise HTTPException(status_code=400, detail="生成报告必须提供 sessionId")

    _, session_owner_id = ensure_actor_can_access_session(request, payload.sessionId)

    async def event_generator() -> AsyncGenerator[str, None]:
        conversation_manager = getattr(request.app.state, "conversation_manager", None)
        workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
        reports_dir = require_state(request, "reports_dir", "报告目录未初始化")
        if conversation_manager is None:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': '对话管理器未初始化'}})}\n\n"
            return

        try:
            profile = to_backend_profile(payload.profile)
            profile_dict = profile_to_dict(profile)
            loop = asyncio.get_running_loop()
            stage_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

            start_event = AgentStatusEvent(
                agent="orchestrator",
                status="running",
                message="开始生成报告...",
            )
            yield f"data: {json.dumps({'type': 'agent_status', 'data': start_event.model_dump()})}\n\n"

            def stage_callback(event: Dict[str, Any]) -> None:
                loop.call_soon_threadsafe(stage_queue.put_nowait, event)

            workflow_task = asyncio.create_task(
                _run_report_workflow(conversation_manager, profile, stage_callback=stage_callback)
            )
            current_stage: Optional[str] = None
            current_stage_message: Optional[str] = None
            current_stage_started_at: Optional[datetime] = None
            heartbeat_seconds = 1.2

            while True:
                if workflow_task.done() and stage_queue.empty():
                    break

                try:
                    stage_event_payload = await asyncio.wait_for(
                        stage_queue.get(),
                        timeout=heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    if workflow_task.done():
                        break
                    if current_stage and current_stage_message and current_stage_started_at is not None:
                        elapsed = int((datetime.now() - current_stage_started_at).total_seconds())
                        heartbeat_event = AgentStatusEvent(
                            agent=current_stage,
                            status="running",
                            message=f"{current_stage_message}（已运行 {elapsed} 秒）",
                        )
                        yield f"data: {json.dumps({'type': 'agent_status', 'data': heartbeat_event.model_dump()})}\n\n"
                    continue

                stage_event = AgentStatusEvent(
                    agent=str(stage_event_payload.get("agent") or "unknown"),
                    status=str(stage_event_payload.get("status") or "unknown"),
                    message=str(stage_event_payload.get("message") or ""),
                )
                logger.info(
                    "Report stream stage event session_id=%s stage=%s status=%s message=%s",
                    payload.sessionId,
                    stage_event.agent,
                    stage_event.status,
                    stage_event.message,
                )
                if stage_event.status == "running":
                    current_stage = stage_event.agent
                    current_stage_message = stage_event.message
                    current_stage_started_at = datetime.now()
                elif stage_event.status in {"completed", "failed"} and stage_event.agent == current_stage:
                    current_stage_message = stage_event.message
                yield f"data: {json.dumps({'type': 'agent_status', 'data': stage_event.model_dump()})}\n\n"

            results = await workflow_task

            report_data = to_frontend_report_data(results)
            save_report_bundle(
                reports_dir=reports_dir,
                workspace_manager=workspace_manager,
                profile=profile_dict,
                results=results,
                report_data=report_data,
                session_id=payload.sessionId,
                user_id=session_owner_id,
            )
            _persist_workspace_snapshot(
                workspace_manager,
                payload.sessionId,
                session_owner_id,
                profile=profile_dict,
            )

            report_text = str(results.get("report") or "")
            if report_text:
                for idx in range(0, len(report_text), 120):
                    chunk = report_text[idx : idx + 120]
                    yield f"data: {json.dumps({'type': 'report_chunk', 'data': {'content': chunk}})}\n\n"
                    await asyncio.sleep(0.015)

            yield f"data: {json.dumps({'type': 'complete', 'data': report_data})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            logger.exception("Report stream failed for session_id=%s", payload.sessionId)
            error_data = json.dumps({"type": "error", "data": {"message": str(exc)}})
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@report_router.post("/generate/{elderly_id}", response_model=ReportGenerateByElderlyResponse)
async def generate_report_for_elderly(
    request: Request,
    elderly_id: str,
    profile_data: Dict[str, Any],
) -> ReportGenerateByElderlyResponse:
    """合并老人画像并生成报告。"""
    ensure_actor_can_access_user(request, elderly_id)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    reports_dir = require_state(request, "reports_dir", "报告目录未初始化")
    store = conversation_manager.store

    if not store.user_exists(elderly_id):
        raise HTTPException(status_code=404, detail="老年人不存在")

    try:
        updates = _extract_profile_updates(profile_data)
        if updates:
            store.update_profile(elderly_id, updates)

        merged_profile = store.get_profile(elderly_id)
        if merged_profile is None:
            raise HTTPException(status_code=404, detail="未找到用户画像")

        session_id = _find_or_create_session_for_user(conversation_manager, workspace_manager, elderly_id)
        merged_profile_dict = asdict(merged_profile)
        _persist_workspace_snapshot(
            workspace_manager,
            session_id,
            elderly_id,
            profile=merged_profile_dict,
        )

        results = await _run_report_workflow(conversation_manager, merged_profile)
        report_data = to_frontend_report_data(results)
        payload = save_report_bundle(
            reports_dir=reports_dir,
            workspace_manager=workspace_manager,
            profile=profile_to_dict(merged_profile),
            results=results,
            report_data=report_data,
            session_id=session_id,
            user_id=elderly_id,
        )

        return ReportGenerateByElderlyResponse(
            reportId=payload["report_id"],
            sessionId=session_id,
            report=ReportData(**report_data),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"报告生成失败: {exc}") from exc


@report_router.get("/{report_id}")
async def get_report(request: Request, report_id: str) -> ReportData:
    """根据报告 ID 返回当前登录主体可访问的标准化报告。"""
    payload = _load_accessible_report_payload(request, report_id)
    report_data = payload.get("report_data")
    if not isinstance(report_data, dict):
        raise HTTPException(status_code=404, detail="报告不存在")
    return ReportData(**report_data)


@report_router.get("/{report_id}/export/pdf")
async def export_report_pdf(request: Request, report_id: str):
    """导出报告为 PDF。"""
    _load_accessible_report_payload(request, report_id)
    raise HTTPException(status_code=501, detail="PDF 导出功能待实现")


@api_router.get("/sessions")
async def list_sessions(request: Request):
    """获取当前主体可见的会话列表。"""
    actor = require_authenticated_actor(request)
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")

    visible_user_ids = _visible_user_ids_for_actor(request, actor)
    sessions_metadata = []
    for session_id in workspace_manager.list_sessions():
        metadata = workspace_manager.get_session_metadata(session_id)
        if metadata and (visible_user_ids is None or metadata.get("user_id") in visible_user_ids):
            sessions_metadata.append(metadata)
    sessions_metadata.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"sessions": sessions_metadata}


@api_router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    """获取当前主体可见的指定会话数据。"""
    _, owner_user_id = ensure_actor_can_view_session(request, session_id)
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")

    metadata = workspace_manager.get_session_metadata(session_id)
    if not metadata or metadata.get("user_id") != owner_user_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    return {
        "metadata": metadata,
        "conversation": workspace_manager.get_conversation(session_id),
        "profile": workspace_manager.get_user_profile(session_id),
        "reports": workspace_manager.get_reports(session_id),
    }


@api_router.post("/sessions/{session_id}/profile")
async def save_session_profile(request: Request, session_id: str, profile: Dict[str, Any]):
    """保存用户画像到工作区。"""
    _, owner_user_id = ensure_actor_can_access_session(request, session_id)
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")

    workspace_manager.save_user_profile(session_id, profile)

    session = conversation_manager.store.get_session(session_id)
    if session is not None:
        _ensure_workspace_metadata(
            workspace_manager,
            session_id,
            owner_user_id,
            created_at=session.get("created_at"),
        )
    workspace_manager.update_metadata(session_id, {"has_profile": True})

    return {"success": True}


@api_router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    """删除指定会话。"""
    ensure_actor_can_access_session(request, session_id)
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    deleted = workspace_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"success": True}


@api_router.get("/health")
async def health_check():
    """健康检查端点。"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "AI 养老健康助手 API",
    }


app.include_router(api_router)
app.include_router(chat_router)
app.include_router(report_router)
app.include_router(auth_router)
app.include_router(family_router)
app.include_router(elderly_router)
app.include_router(doctor_router)


def main():
    """启动服务器。"""
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
