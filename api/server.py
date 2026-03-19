#!/usr/bin/env python3
"""
FastAPI 服务器入口。
"""

from __future__ import annotations

import asyncio
import json
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
sys.path.insert(0, str(code_dir))
sys.path.insert(0, str(api_dir))
sys.path.insert(0, str(core_dir))

from auth_routes import auth_router
from family_routes import family_router
from mappers import to_backend_profile, to_frontend_report_data
from memory.conversation_manager import ConversationManager, SessionState
from memory.family_caregiver_manager import FamilyCaregiverManager
from report_utils import profile_to_dict, save_report_bundle
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
from workspace_manager import WorkspaceManager

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


DB_PATH = "/tmp/elderly-care-db/users.db"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "reports"
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


def _require_state(request: Request, attr: str, error_message: str):
    manager = getattr(request.app.state, attr, None)
    if manager is None:
        raise HTTPException(status_code=500, detail=error_message)
    return manager


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


async def _run_report_workflow(
    conversation_manager: ConversationManager,
    profile,
) -> Dict[str, Any]:
    return await asyncio.to_thread(conversation_manager.orchestrator.run, profile, False)


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
    """开始新的健康评估对话。"""
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = _require_state(request, "workspace_manager", "工作区管理器未初始化")

    user_id = conversation_manager.new_user()
    session_id = conversation_manager.new_session(user_id)
    result = conversation_manager.chat(session_id, "")
    history = conversation_manager.get_history(session_id)

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
    )


@chat_router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: Request, payload: ChatMessageRequest) -> ChatMessageResponse:
    """发送聊天消息。"""
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = getattr(request.app.state, "workspace_manager", None)

    try:
        result = conversation_manager.chat(payload.sessionId, payload.message)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"处理消息失败: {exc}") from exc

    state = result.get("state")
    completed = state == SessionState.REPORT_DONE

    if workspace_manager is not None:
        session = conversation_manager.store.get_session(payload.sessionId)
        if session is not None:
            _persist_workspace_snapshot(
                workspace_manager,
                payload.sessionId,
                session["user_id"],
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
    """获取对话历史。"""
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")
    session = conversation_manager.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _serialize_history(conversation_manager.store.get_session_history(session_id))


@chat_router.get("/progress/{session_id}", response_model=ChatProgressResponse)
async def get_chat_progress(request: Request, session_id: str) -> ChatProgressResponse:
    """获取会话当前进度。"""
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")

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
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")

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

    async def event_generator() -> AsyncGenerator[str, None]:
        conversation_manager = getattr(request.app.state, "conversation_manager", None)
        if conversation_manager is None:
            yield f"data: {json.dumps({'error': '对话管理器未初始化'})}\n\n"
            return

        try:
            result = conversation_manager.chat(sessionId, message)
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
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = getattr(request.app.state, "workspace_manager", None)

    try:
        profile = to_backend_profile(payload.profile)
        results = await _run_report_workflow(conversation_manager, profile)
        report_data = to_frontend_report_data(results)
        save_report_bundle(
            reports_dir=REPORTS_DIR,
            workspace_manager=workspace_manager,
            profile=profile_to_dict(profile),
            results=results,
            report_data=report_data,
            session_id=payload.sessionId,
        )
        if payload.sessionId and workspace_manager is not None:
            session = conversation_manager.store.get_session(payload.sessionId)
            if session is not None:
                _persist_workspace_snapshot(
                    workspace_manager,
                    payload.sessionId,
                    session["user_id"],
                    profile=profile_to_dict(profile),
                )
        return ReportData(**report_data)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {exc}") from exc


@report_router.post("/stream")
async def stream_report(request: Request, payload: ReportGenerateRequest):
    """流式生成报告 (SSE)。"""

    async def event_generator() -> AsyncGenerator[str, None]:
        conversation_manager = getattr(request.app.state, "conversation_manager", None)
        workspace_manager = getattr(request.app.state, "workspace_manager", None)
        if conversation_manager is None:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': '对话管理器未初始化'}})}\n\n"
            return

        try:
            profile = to_backend_profile(payload.profile)
            profile_dict = profile_to_dict(profile)

            start_event = AgentStatusEvent(
                agent="orchestrator",
                status="running",
                message="开始生成报告...",
            )
            yield f"data: {json.dumps({'type': 'agent_status', 'data': start_event.model_dump()})}\n\n"

            workflow_task = asyncio.create_task(_run_report_workflow(conversation_manager, profile))

            agent_stages = ["status", "risk", "factors"]
            if RAG_ENABLED:
                agent_stages.append("knowledge")
            agent_stages.extend(["actions", "priority", "review", "report"])
            stage_messages = {
                "status": "正在判定功能状态...",
                "risk": "正在进行风险预测...",
                "factors": "正在提取关键影响因素...",
                "knowledge": "正在检索知识库参考...",
                "actions": "正在生成干预行动建议...",
                "priority": "正在排序建议优先级...",
                "review": "正在进行结果复核...",
                "report": "正在整理最终报告文本...",
            }

            stage_idx = 0
            stage_start_ts = datetime.now()
            stage_switch_seconds = 6
            heartbeat_seconds = 1.2

            current_stage = agent_stages[stage_idx]
            initial_event = AgentStatusEvent(
                agent=current_stage,
                status="running",
                message=stage_messages[current_stage],
            )
            yield f"data: {json.dumps({'type': 'agent_status', 'data': initial_event.model_dump()})}\n\n"

            while not workflow_task.done():
                await asyncio.sleep(heartbeat_seconds)
                elapsed = (datetime.now() - stage_start_ts).total_seconds()
                next_stage_threshold = (stage_idx + 1) * stage_switch_seconds

                if stage_idx < len(agent_stages) - 1 and elapsed >= next_stage_threshold:
                    finished_stage = agent_stages[stage_idx]
                    completed_event = AgentStatusEvent(
                        agent=finished_stage,
                        status="completed",
                        message=f"{finished_stage} 阶段完成",
                    )
                    yield f"data: {json.dumps({'type': 'agent_status', 'data': completed_event.model_dump()})}\n\n"

                    stage_idx += 1
                    current_stage = agent_stages[stage_idx]
                    running_event = AgentStatusEvent(
                        agent=current_stage,
                        status="running",
                        message=stage_messages[current_stage],
                    )
                    yield f"data: {json.dumps({'type': 'agent_status', 'data': running_event.model_dump()})}\n\n"
                else:
                    heartbeat_event = AgentStatusEvent(
                        agent=agent_stages[stage_idx],
                        status="running",
                        message=f"{stage_messages[agent_stages[stage_idx]]}（已运行 {int(elapsed)} 秒）",
                    )
                    yield f"data: {json.dumps({'type': 'agent_status', 'data': heartbeat_event.model_dump()})}\n\n"

            results = await workflow_task

            done_stage = agent_stages[stage_idx]
            final_stage_event = AgentStatusEvent(
                agent=done_stage,
                status="completed",
                message=f"{done_stage} 阶段完成",
            )
            yield f"data: {json.dumps({'type': 'agent_status', 'data': final_stage_event.model_dump()})}\n\n"

            report_data = to_frontend_report_data(results)
            save_report_bundle(
                reports_dir=REPORTS_DIR,
                workspace_manager=workspace_manager,
                profile=profile_dict,
                results=results,
                report_data=report_data,
                session_id=payload.sessionId,
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
    conversation_manager = _require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = _require_state(request, "workspace_manager", "工作区管理器未初始化")
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
            reports_dir=REPORTS_DIR,
            workspace_manager=workspace_manager,
            profile=profile_to_dict(merged_profile),
            results=results,
            report_data=report_data,
            session_id=session_id,
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


def _load_report_payload(report_id: str, workspace_manager: WorkspaceManager | None) -> Dict[str, Any] | None:
    for report_file in REPORTS_DIR.rglob("*.json"):
        with open(report_file, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if payload.get("report_id") == report_id:
            return payload

    if workspace_manager is None:
        return None

    for session_id in workspace_manager.list_sessions():
        for report_file in workspace_manager.get_report_files(session_id):
            with open(report_file, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            if payload.get("report_id") == report_id:
                return payload
    return None


@report_router.get("/{report_id}")
async def get_report(request: Request, report_id: str) -> ReportData:
    """根据报告 ID 返回标准化报告数据。"""
    workspace_manager = getattr(request.app.state, "workspace_manager", None)
    payload = _load_report_payload(report_id, workspace_manager)
    if payload is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    report_data = payload.get("report_data")
    if not isinstance(report_data, dict):
        raise HTTPException(status_code=404, detail="报告不存在")
    return ReportData(**report_data)


@report_router.get("/{report_id}/export/pdf")
async def export_report_pdf(report_id: str):
    """导出报告为 PDF。"""
    raise HTTPException(status_code=501, detail="PDF 导出功能待实现")


@api_router.get("/sessions")
async def list_sessions(request: Request):
    """获取所有会话列表。"""
    workspace_manager = _require_state(request, "workspace_manager", "工作区管理器未初始化")

    sessions_metadata = []
    for session_id in workspace_manager.list_sessions():
        metadata = workspace_manager.get_session_metadata(session_id)
        if metadata:
            sessions_metadata.append(metadata)
    sessions_metadata.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {"sessions": sessions_metadata}


@api_router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    """获取指定会话的完整数据。"""
    workspace_manager = _require_state(request, "workspace_manager", "工作区管理器未初始化")

    metadata = workspace_manager.get_session_metadata(session_id)
    if not metadata:
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
    workspace_manager = _require_state(request, "workspace_manager", "工作区管理器未初始化")
    conversation_manager = getattr(request.app.state, "conversation_manager", None)

    workspace_manager.save_user_profile(session_id, profile)

    session = conversation_manager.store.get_session(session_id) if conversation_manager is not None else None
    if session is not None:
        _ensure_workspace_metadata(workspace_manager, session_id, session["user_id"], created_at=session.get("created_at"))
    workspace_manager.update_metadata(session_id, {"has_profile": True})

    return {"success": True}


@api_router.delete("/sessions/{session_id}")
async def delete_session(request: Request, session_id: str):
    """删除指定会话。"""
    workspace_manager = _require_state(request, "workspace_manager", "工作区管理器未初始化")
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
