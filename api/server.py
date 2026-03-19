#!/usr/bin/env python3
"""
FastAPI 服务器
为前端提供 Web API 接口
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

# 首先加载 .env 文件（必须在导入其他模块之前）
try:
    from dotenv import load_dotenv
    # 加载 .env 文件
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    # dotenv 不可用，继续使用环境变量
    pass

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState

import sys

# 添加 code 目录和 api 目录到 Python 路径
code_dir = Path(__file__).parent.parent / "code"
api_dir = Path(__file__).parent
core_dir = Path(__file__).parent.parent / "core"
sys.path.insert(0, str(code_dir))
sys.path.insert(0, str(api_dir))
sys.path.insert(0, str(core_dir))

# 数据库路径（必须在导入之前定义）
DB_PATH = "/tmp/elderly-care-db/users.db"

# 初始化数据库（注释掉，让 user_profile_store 自己初始化）
# from db_migrations import init_auth_tables
# init_auth_tables(str(DB_PATH))

from memory.conversation_manager import ConversationManager, SessionState
from workspace_manager import WorkspaceManager
from mappers import to_backend_profile, to_frontend_report_data
from services.google_stt_stream import GoogleSpeechStreamBridge, GoogleSpeechStreamConfig, GoogleSpeechStreamError
from schemas import (
    AgentStatusEvent,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatProgressResponse,
    ChatStartResponse,
    ReportData,
    ReportGenerateRequest,
)
from family_routes import auth_router, family_router


# 报告保存目录
REPORTS_DIR = Path(__file__).parent.parent / "data" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# 全局对话管理器
conversation_manager: Optional[ConversationManager] = None

# 全局工作区管理器
workspace_manager: Optional[WorkspaceManager] = None
RAG_ENABLED = os.getenv("RAG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


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


def build_google_speech_config(language_code: Optional[str] = None) -> GoogleSpeechStreamConfig:
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


def save_report(
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    session_id: Optional[str] = None
) -> str:
    """
    保存报告到文件系统

    保存为 JSON 和 Markdown 两种格式

    Args:
        profile: 用户档案数据
        results: 多智能体系统的原始结果
        report_data: 转换后的前端报告数据
        session_id: 会话 ID（可选）

    Returns:
        报告 ID（基于时间戳的唯一标识）
    """
    # 生成报告 ID
    timestamp = datetime.now()
    report_id = timestamp.strftime("%Y%m%d_%H%M%S")

    # 从 profile 中获取基本信息用于文件名
    age = profile.get("age", "未知")
    sex = profile.get("sex", "未知")

    # 创建子目录（按日期）
    date_dir = REPORTS_DIR / timestamp.strftime("%Y%m")
    date_dir.mkdir(exist_ok=True)

    # 文件名
    base_filename = f"report_{report_id}_{age}岁{sex}"

    # 准备报告数据
    json_data = {
        "report_id": report_id,
        "session_id": session_id,
        "generated_at": timestamp.isoformat(),
        "profile": profile,
        "raw_results": results,
        "report_data": report_data,
    }

    # 1. 保存 JSON 格式（包含完整数据）到传统位置
    json_file = date_dir / f"{base_filename}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    # 2. 保存 Markdown 格式（可读性好的报告）到传统位置
    md_file = date_dir / f"{base_filename}.md"
    md_content = _generate_markdown_report(profile, results, report_data, timestamp)
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"✅ 报告已保存:")
    print(f"   JSON: {json_file}")
    print(f"   Markdown: {md_file}")

    # 3. 如果有 session_id，也保存到工作区
    if session_id and workspace_manager is not None:
        workspace_manager.save_report(session_id, json_data, "json")
        workspace_manager.save_report(session_id, md_content, "md")
        workspace_manager.update_metadata(session_id, {"has_report": True})
        print(f"   工作区: {workspace_manager.get_session_dir(session_id)}")

    return report_id


def _generate_markdown_report(
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    timestamp: datetime
) -> str:
    """生成 Markdown 格式的健康报告"""

    # 获取各部分数据
    status = results.get("status", {})
    risk = results.get("risk", {})
    factors = results.get("factors", {})
    raw_report = results.get("report", "")

    md_lines = []
    md_lines.append("# 养老健康评估报告")
    md_lines.append("")

    # 报告头部信息
    md_lines.append("## 报告信息")
    md_lines.append("")
    md_lines.append(f"- **生成时间**: {timestamp.strftime('%Y年%m月%d日 %H:%M')}")
    md_lines.append(f"- **年龄**: {profile.get('age', '未知')}岁")
    md_lines.append(f"- **性别**: {profile.get('sex', '未知')}")
    md_lines.append("")

    # 1. 健康报告总结
    md_lines.append("## 1. 健康报告总结")
    md_lines.append("")

    # 从 raw_report 中提取总结部分
    if raw_report:
        import re
        summary_match = re.search(r'##\s*1\.\s*健康报告总结\s*(.+?)(?:\n##\s|\Z)', raw_report, re.S)
        if summary_match:
            summary_text = summary_match.group(1).strip()
            md_lines.append(summary_text)
        else:
            md_lines.append(report_data.get("summary", "暂无总结"))
    else:
        md_lines.append(report_data.get("summary", "暂无总结"))
    md_lines.append("")

    # 2. 功能状态评估
    md_lines.append("## 2. 功能状态评估")
    md_lines.append("")

    if status:
        md_lines.append(f"**失能等级**: {status.get('status_level', '未知')}")
        md_lines.append(f"**BADL得分**: {status.get('badl_score', '未知')}/6")
        md_lines.append(f"**IADL得分**: {status.get('iadl_score', '未知')}/8")
        md_lines.append("")
        md_lines.append(f"**状态描述**: {status.get('status_description', '无')}")
    md_lines.append("")

    # 健康画像
    health_portrait = report_data.get("healthPortrait", {})
    if health_portrait:
        md_lines.append("### 健康画像")
        md_lines.append("")
        md_lines.append(f"**功能状态**: {health_portrait.get('functionalStatus', '无描述')}")
        md_lines.append("")

        # 优势
        strengths = health_portrait.get("strengths", [])
        if strengths:
            md_lines.append("**优势**:")
            for strength in strengths:
                md_lines.append(f"- ✅ {strength}")
            md_lines.append("")

        # 问题
        problems = health_portrait.get("problems", [])
        if problems:
            md_lines.append("**需要关注的问题**:")
            for problem in problems:
                md_lines.append(f"- ⚠️ {problem}")
            md_lines.append("")

    # 3. 风险预测分析
    md_lines.append("## 3. 风险预测分析")
    md_lines.append("")

    risk_factors = report_data.get("riskFactors", {})

    # 短期风险
    short_term = risk_factors.get("shortTerm", [])
    if short_term:
        md_lines.append("### 短期风险（1-4周）")
        md_lines.append("")
        for risk in short_term:
            level_icon = "🔴" if risk["level"] == "high" else "🟡" if risk["level"] == "medium" else "🟢"
            md_lines.append(f"#### {level_icon} {risk['name']}")
            md_lines.append(f"- **风险等级**: {risk['level']}")
            md_lines.append(f"- **时间范围**: {risk['timeframe']}")
            md_lines.append(f"- **描述**: {risk['description']}")
            md_lines.append("")

    # 中期风险
    mid_term = risk_factors.get("midTerm", [])
    if mid_term:
        md_lines.append("### 中期风险（1-6月）")
        md_lines.append("")
        for risk in mid_term:
            level_icon = "🔴" if risk["level"] == "high" else "🟡" if risk["level"] == "medium" else "🟢"
            md_lines.append(f"#### {level_icon} {risk['name']}")
            md_lines.append(f"- **风险等级**: {risk['level']}")
            md_lines.append(f"- **时间范围**: {risk['timeframe']}")
            md_lines.append(f"- **描述**: {risk['description']}")
            md_lines.append("")

    # 风险总结
    if risk:
        md_lines.append("**风险总结**:")
        md_lines.append(f"- 短期风险数: {len(short_term)}项")
        md_lines.append(f"- 中期风险数: {len(mid_term)}项")
        md_lines.append(f"- 风险概况: {risk.get('risk_summary', '无')}")
        md_lines.append("")

    # 4. 行动建议
    md_lines.append("## 4. 行动建议")
    md_lines.append("")

    recommendations = report_data.get("recommendations", {})

    # 优先级 A（立即执行）
    priority1 = recommendations.get("priority1", [])
    if priority1:
        md_lines.append("### 优先级 A - 立即执行")
        md_lines.append("")
        for rec in priority1:
            md_lines.append(f"#### {rec['title']}")
            md_lines.append(f"- **类别**: {rec['category']}")
            md_lines.append(f"- **描述**: {rec['description']}")
            md_lines.append("")

    # 优先级 B（本周完成）
    priority2 = recommendations.get("priority2", [])
    if priority2:
        md_lines.append("### 优先级 B - 本周完成")
        md_lines.append("")
        for rec in priority2:
            md_lines.append(f"#### {rec['title']}")
            md_lines.append(f"- **类别**: {rec['category']}")
            md_lines.append(f"- **描述**: {rec['description']}")
            md_lines.append("")

    # 优先级 C（后续跟进）
    priority3 = recommendations.get("priority3", [])
    if priority3:
        md_lines.append("### 优先级 C - 后续跟进")
        md_lines.append("")
        for rec in priority3:
            md_lines.append(f"#### {rec['title']}")
            md_lines.append(f"- **类别**: {rec['category']}")
            md_lines.append(f"- **描述**: {rec['description']}")
            md_lines.append("")

    # 5. 完整报告（如果有的话）
    if raw_report:
        md_lines.append("## 5. 完整评估报告")
        md_lines.append("")
        md_lines.append(raw_report)
        md_lines.append("")

    # 报告尾部
    md_lines.append("---")
    md_lines.append("")
    md_lines.append("*本报告由 AI 养老健康助手自动生成，仅供参考。请结合专业医生的诊断和建议。*")

    return "\n".join(md_lines)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global conversation_manager, workspace_manager

    try:
        # 确保 data 目录存在
        db_path = "/tmp/elderly-care-db/users.db"
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化对话管理器
        conversation_manager = ConversationManager(db_path=db_path)

        # 初始化家属端管理器
        from memory.family_caregiver_manager import FamilyCaregiverManager
        family_manager = FamilyCaregiverManager(db_path=db_path)
        
        # 设置全局 family_manager
        from api.family_routes import set_family_manager
        set_family_manager(family_manager)

        # 初始化工作区管理器
        backend_root = Path(__file__).parent.parent
        workspace_manager = WorkspaceManager(base_dir=str(backend_root / "workspace"))

        print("✓ FastAPI 服务器已启动")
        print(f"✓ 数据库路径: {db_path}")
        print(f"✓ 工作区路径: {workspace_manager.base_dir}")
    except Exception as e:
        print(f"✗ 启动失败: {e}")
        import traceback
        traceback.print_exc()

    yield

    print("FastAPI 服务器正在关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="AI 养老健康助手 API",
    description="提供健康评估对话和报告生成服务",
    version="1.0.0",
    lifespan=lifespan,
)

# 配置 CORS（必须在最前面）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源（开发环境）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 路由定义 ====================

# 创建路由器
api_router = APIRouter(prefix="/api")
chat_router = APIRouter(prefix="/chat")
report_router = APIRouter(prefix="/report")


# ==================== 聊天相关 API ====================


@chat_router.post("/start", response_model=ChatStartResponse)
async def start_chat() -> ChatStartResponse:
    """
    开始新的健康评估对话

    创建新用户和新会话，返回欢迎消息
    """
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")
    if workspace_manager is None:
        raise HTTPException(status_code=500, detail="工作区管理器未初始化")

    # 创建新用户
    user_id = conversation_manager.new_user()

    # 创建新会话
    session_id = conversation_manager.new_session(user_id)

    # 获取欢迎消息
    result = conversation_manager.chat(session_id, "")

    # 创建工作区元数据
    workspace_manager.create_metadata(session_id, {
        "session_id": session_id,
        "user_id": user_id,
        "created_at": datetime.now().isoformat(),
        "status": "active",
        "title": f"评估记录 - {datetime.now().strftime('%m-%d %H:%M')}"
    })

    return ChatStartResponse(
        userId=user_id,
        sessionId=session_id,
        welcomeMessage=result.get("reply", "您好！我是AI养老健康助手。"),
    )


@chat_router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest) -> ChatMessageResponse:
    """
    发送聊天消息

    处理用户输入并返回AI回复
    """
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")

    try:
        result = conversation_manager.chat(request.sessionId, request.message)

        state = result.get("state")
        progress = result.get("progress", 0.0)
        completed = state == SessionState.REPORT_DONE

        # 转换状态为字符串
        state_map = {
            SessionState.GREETING: "greeting",
            SessionState.COLLECTING: "collecting",
            SessionState.CONFIRMING: "confirming",
            SessionState.GENERATING: "generating",
            SessionState.REPORT_DONE: "completed",
            SessionState.FOLLOW_UP: "follow_up",
        }

        # 保存对话历史到工作区
        if workspace_manager is not None:
            history = conversation_manager.get_history(request.sessionId)
            messages = [
                {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "timestamp": msg.get("timestamp")
                }
                for msg in history
            ]
            workspace_manager.save_conversation(request.sessionId, messages)

        return ChatMessageResponse(
            message=result.get("reply", ""),
            state=state_map.get(state, "unknown"),
            progress=progress,
            completed=completed,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"处理消息失败: {str(e)}")


@chat_router.get("/history/{session_id}")
async def get_chat_history(session_id: str) -> List[Dict[str, Any]]:
    """
    获取对话历史

    返回指定会话的所有消息记录
    """
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")

    try:
        # 从数据库获取会话历史
        history = conversation_manager.store.get_session_history(session_id)

        # 转换为前端格式
        messages = []
        for msg in history:
            messages.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", ""),
                "timestamp": msg.get("timestamp"),
            })

        return messages
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")


@chat_router.get("/progress/{session_id}", response_model=ChatProgressResponse)
async def get_chat_progress(session_id: str) -> ChatProgressResponse:
    """
    获取会话当前进度
    """
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")

    try:
        # 简化版本：直接返回基本进度信息
        return ChatProgressResponse(
            state="collecting",
            progress=0.0,
            completedGroups=[],
            pendingGroups=[],
            missingFields={},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取进度失败: {str(e)}")


@chat_router.get("/profile/{session_id}")
async def get_chat_profile(session_id: str) -> Dict[str, Any]:
    """
    获取会话当前用户画像

    返回用于报告生成的画像字段
    """
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")

    try:
        profile = conversation_manager.get_profile(session_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="未找到用户画像")
        return profile
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取画像失败: {str(e)}")


@chat_router.get("/stream")
async def stream_chat(message: str, sessionId: str):
    """
    流式聊天 (SSE)

    使用 Server-Sent Events 实现流式响应
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        if conversation_manager is None:
            yield f"data: {json.dumps({'error': '对话管理器未初始化'})}\n\n"
            return

        try:
            result = conversation_manager.chat(sessionId, message)
            reply = result.get("reply", "")

            # 模拟流式输出（按字符分割）
            for i, char in enumerate(reply):
                chunk_data = json.dumps({"content": char})
                yield f"data: {chunk_data}\n\n"
                await asyncio.sleep(0.02)  # 模拟打字效果

            # 发送完成信号
            yield f"data: [DONE]\n\n"

        except Exception as e:
            error_data = json.dumps({"error": str(e)})
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


@app.websocket("/ws/stt")
async def stream_speech_to_text(websocket: WebSocket):
    await websocket.accept()

    bridge: Optional[GoogleSpeechStreamBridge] = None
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
        start_payload = await websocket.receive_json()
        if start_payload.get("type") != "start":
            await websocket.send_json({"type": "error", "message": "Invalid speech start message."})
            await websocket.close(code=1003)
            return

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


# ==================== 报告相关 API ====================


@report_router.post("/generate", response_model=ReportData)
async def generate_report(request: ReportGenerateRequest) -> ReportData:
    """
    生成健康评估报告

    根据用户档案数据生成完整的健康报告，并自动保存到文件系统
    """
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")

    try:
        # 将前端数据转换为后端 UserProfile
        profile = to_backend_profile(request.profile)

        # 转换为字典用于保存
        profile_dict = {
            "age": profile.age,
            "sex": profile.sex,
            "residence": profile.residence,
            "education_years": profile.education_years,
            "marital_status": profile.marital_status,
            # 添加其他需要的字段...
        }

        # 导入多智能体系统
        from multi_agent_system_v2 import OrchestratorAgentV2

        # 创建编排器并运行
        orchestrator = OrchestratorAgentV2()
        results = orchestrator.run(profile)

        # 转换为前端报告格式
        report_data = to_frontend_report_data(results)

        # 保存报告到文件系统
        save_report(
            profile=profile_dict,
            results=results,
            report_data=report_data,
            session_id=request.sessionId
        )

        return ReportData(**report_data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {str(e)}")


@report_router.post("/stream")
async def stream_report(request: ReportGenerateRequest):
    """
    流式生成报告 (SSE)

    使用 Server-Sent Events 实现流式报告生成，并自动保存到文件系统
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # 转换数据格式
            profile = to_backend_profile(request.profile)

            # 转换为字典用于保存
            profile_dict = {
                "age": profile.age,
                "sex": profile.sex,
                "residence": profile.residence,
                "education_years": profile.education_years,
                "marital_status": profile.marital_status,
            }

            # 导入多智能体系统
            from multi_agent_system_v2 import OrchestratorAgentV2

            # 创建编排器
            orchestrator = OrchestratorAgentV2()

            # 发送开始状态
            start_event = AgentStatusEvent(
                agent="orchestrator", status="running", message="开始生成报告..."
            )
            yield f"data: {json.dumps({'type': 'agent_status', 'data': start_event.model_dump()})}\n\n"

            # 将耗时工作流放入后台线程，主协程持续推送进度事件，避免前端长时间无响应
            workflow_task = asyncio.create_task(asyncio.to_thread(orchestrator.run, profile))

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

            # 初始阶段事件
            current_stage = agent_stages[stage_idx]
            yield f"data: {json.dumps({'type': 'agent_status', 'data': AgentStatusEvent(agent=current_stage, status='running', message=stage_messages[current_stage]).model_dump()})}\n\n"

            while not workflow_task.done():
                await asyncio.sleep(heartbeat_seconds)

                elapsed = (datetime.now() - stage_start_ts).total_seconds()
                next_stage_threshold = (stage_idx + 1) * stage_switch_seconds

                # 按时间推进阶段，保持前端持续收到流式事件
                if stage_idx < len(agent_stages) - 1 and elapsed >= next_stage_threshold:
                    finished_stage = agent_stages[stage_idx]
                    yield f"data: {json.dumps({'type': 'agent_status', 'data': AgentStatusEvent(agent=finished_stage, status='completed', message=f'{finished_stage} 阶段完成').model_dump()})}\n\n"

                    stage_idx += 1
                    current_stage = agent_stages[stage_idx]
                    yield f"data: {json.dumps({'type': 'agent_status', 'data': AgentStatusEvent(agent=current_stage, status='running', message=stage_messages[current_stage]).model_dump()})}\n\n"
                else:
                    # 心跳事件，避免前端等待期无输出
                    current_stage = agent_stages[stage_idx]
                    msg = f"{stage_messages[current_stage]}（已运行 {int(elapsed)} 秒）"
                    yield f"data: {json.dumps({'type': 'agent_status', 'data': AgentStatusEvent(agent=current_stage, status='running', message=msg).model_dump()})}\n\n"

            # 获取最终结果（若线程异常，会在此抛错并进入 except）
            results = await workflow_task

            # 当前阶段补发完成事件
            done_stage = agent_stages[stage_idx]
            yield f"data: {json.dumps({'type': 'agent_status', 'data': AgentStatusEvent(agent=done_stage, status='completed', message=f'{done_stage} 阶段完成').model_dump()})}\n\n"

            # 转换为前端报告格式
            report_data = to_frontend_report_data(results)

            # 保存报告到文件系统
            save_report(
                profile=profile_dict,
                results=results,
                report_data=report_data,
                session_id=request.sessionId
            )

            # 流式推送报告正文（Markdown 文本分块），提升长文本展示体验
            report_text = str(results.get("report") or "")
            if report_text:
                chunk_size = 120
                for idx in range(0, len(report_text), chunk_size):
                    chunk = report_text[idx: idx + chunk_size]
                    yield f"data: {json.dumps({'type': 'report_chunk', 'data': {'content': chunk}})}\n\n"
                    await asyncio.sleep(0.015)

            # 发送完整报告
            yield f"data: {json.dumps({'type': 'complete', 'data': report_data})}\n\n"
            yield f"data: [DONE]\n\n"

        except Exception as e:
            error_data = json.dumps({"type": "error", "data": {"message": str(e)}})
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


@report_router.get("/{report_id}")
async def get_report(report_id: str) -> ReportData:
    """
    获取已生成的报告

    根据报告ID返回报告数据
    """
    # 这里可以从数据库或文件中读取已保存的报告
    # 暂时返回错误提示
    raise HTTPException(status_code=404, detail="报告不存在")


@report_router.get("/{report_id}/export/pdf")
async def export_report_pdf(report_id: str):
    """
    导出报告为 PDF

    生成并下载 PDF 格式的健康报告
    """
    # 这里可以使用 reportlab 或其他库生成 PDF
    raise HTTPException(status_code=501, detail="PDF 导出功能待实现")


# ==================== 历史记录 API ====================


@api_router.get("/sessions")
async def list_sessions():
    """获取所有会话列表"""
    if workspace_manager is None:
        raise HTTPException(status_code=500, detail="工作区管理器未初始化")

    sessions_list = workspace_manager.list_sessions()
    sessions_metadata = []
    for session_id in sessions_list:
        metadata = workspace_manager.get_session_metadata(session_id)
        sessions_metadata.append(metadata)
    return {"sessions": sorted(sessions_metadata, key=lambda x: x["created_at"], reverse=True)}


@api_router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取指定会话的完整数据"""
    if workspace_manager is None:
        raise HTTPException(status_code=500, detail="工作区管理器未初始化")

    result = {
        "metadata": workspace_manager.get_session_metadata(session_id),
        "conversation": None,
        "profile": None,
        "reports": []
    }

    # 读取对话历史
    conversation = workspace_manager.get_conversation(session_id)
    if conversation:
        result["conversation"] = conversation

    # 读取用户画像
    profile = workspace_manager.get_user_profile(session_id)
    if profile:
        result["profile"] = profile

    # 读取报告列表
    result["reports"] = workspace_manager.get_reports(session_id)

    return result


@api_router.post("/sessions/{session_id}/profile")
async def save_session_profile(session_id: str, profile: Dict[str, Any]):
    """保存用户画像到工作区"""
    if workspace_manager is None:
        raise HTTPException(status_code=500, detail="工作区管理器未初始化")

    workspace_manager.save_user_profile(session_id, profile)

    # 更新元数据
    workspace_manager.update_metadata(session_id, {"has_profile": True})

    return {"success": True}


@api_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除指定会话"""
    if workspace_manager is None:
        raise HTTPException(status_code=500, detail="工作区管理器未初始化")

    workspace_manager.delete_session(session_id)
    return {"success": True}


# ==================== 健康检查 ====================


@api_router.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "AI 养老健康助手 API",
    }


# ==================== 家属端 API ====================

family_router = APIRouter(prefix="/family")


@family_router.get("/elderly-list")
async def get_elderly_list():
    """获取所有老年人列表（家属端）"""
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")
    
    try:
        # 从数据库直接查询所有用户
        store = conversation_manager.store
        
        # 获取所有用户（通过查询数据库）
        import sqlite3
        conn = sqlite3.connect(store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id, profile FROM users")
        rows = cursor.fetchall()
        conn.close()
        
        elderly_list = []
        for row in rows:
            user_id = row[0]
            profile_json = row[1]
            
            if profile_json:
                import json
                profile = json.loads(profile_json)
                elderly_list.append({
                    "elderly_id": user_id,
                    "name": profile.get("name", "未命名"),
                    "relation": "家庭成员",
                    "completion_rate": 0.8,  # 演示数据
                    "created_at": datetime.now().isoformat()
                })
        
        return {"data": elderly_list}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"获取列表失败: {str(e)}")


@family_router.get("/elderly/{elderly_id}")
async def get_elderly_detail(elderly_id: str):
    """获取老年人详细信息"""
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")
    
    try:
        store = conversation_manager.store
        profile = store.get_profile(elderly_id)
        
        if not profile:
            raise HTTPException(status_code=404, detail="老年人不存在")
        
        from dataclasses import asdict
        return {
            "elderly_id": elderly_id,
            "profile": asdict(profile) if profile else {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取详情失败: {str(e)}")


@family_router.put("/elderly/{elderly_id}")
async def update_elderly_info(elderly_id: str, updates: Dict[str, Any]):
    """更新老年人信息"""
    if conversation_manager is None:
        raise HTTPException(status_code=500, detail="对话管理器未初始化")
    
    try:
        store = conversation_manager.store
        store.update_profile(elderly_id, updates)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@family_router.get("/reports/{elderly_id}")
async def get_elderly_reports(elderly_id: str):
    """获取老年人的所有报告"""
    if workspace_manager is None:
        raise HTTPException(status_code=500, detail="工作区管理器未初始化")
    
    try:
        # 从工作区获取报告
        reports = []
        workspace_path = Path(workspace_manager.base_dir) / elderly_id
        
        if workspace_path.exists():
            report_files = list(workspace_path.glob("*_report.json"))
            for report_file in report_files:
                with open(report_file, 'r', encoding='utf-8') as f:
                    report_data = json.load(f)
                    reports.append({
                        "id": report_file.stem,
                        "title": report_data.get("title", "健康评估报告"),
                        "created_at": report_file.stat().st_mtime,
                        "content": report_data
                    })
        
        return {"data": reports}
    except Exception as e:
        return {"data": []}


# ==================== 报告生成 API ====================

report_router = APIRouter(prefix="/report")


@report_router.post("/generate/{elderly_id}")
async def generate_report(elderly_id: str, profile_data: Dict[str, Any]):
    """生成健康评估报告"""
    if orchestrator is None:
        raise HTTPException(status_code=500, detail="工作流引擎未初始化")
    
    try:
        # 调用工作流生成报告
        result = orchestrator.run_workflow(profile_data)
        
        # 保存报告到工作区
        if workspace_manager:
            report_path = Path(workspace_manager.base_dir) / elderly_id / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        
        return {
            "success": True,
            "report": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"报告生成失败: {str(e)}")


class LoginRequest(BaseModel):
    phone: str
    password: str


@auth_router.post("/login")
async def login(request: LoginRequest):
    """简单的登录接口（演示用）"""
    # 演示用：任何手机号和密码都可以登录
    if not request.phone or not request.password:
        raise HTTPException(status_code=400, detail="手机号和密码不能为空")
    
    # 生成简单的 token
    token = f"token_{request.phone}_{datetime.now().timestamp()}"
    
    return {
        "token": token,
        "user_name": f"用户{request.phone[-4:]}",
        "role": "family"
    }


@auth_router.post("/logout")
async def logout():
    """登出接口"""
    return {"success": True}


# ==================== 注册路由 ====================

app.include_router(api_router)  # api_router 已经有 prefix="/api"
app.include_router(chat_router)
app.include_router(report_router)
app.include_router(auth_router)  # 认证路由
app.include_router(family_router)  # 家属端路由


# ==================== 主程序入口 ====================


def main():
    """启动服务器"""
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
