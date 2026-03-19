"""
家属端 API 路由。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request


family_router = APIRouter(prefix="/family")


def _require_state(request: Request, attr: str, error_message: str):
    manager = getattr(request.app.state, attr, None)
    if manager is None:
        raise HTTPException(status_code=500, detail=error_message)
    return manager


def _get_family_manager(request: Request):
    return _require_state(request, "family_manager", "家属管理器未初始化")


def _get_conversation_manager(request: Request):
    return _require_state(request, "conversation_manager", "对话管理器未初始化")


def _get_workspace_manager(request: Request):
    return _require_state(request, "workspace_manager", "工作区管理器未初始化")


@family_router.post("/session/start/{elderly_id}")
async def start_family_session(request: Request, elderly_id: str):
    """为老人启动家属端评估会话。"""
    family_manager = _get_family_manager(request)

    try:
        session_id = family_manager.new_family_session(elderly_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动会话失败: {exc}") from exc

    greeting = (
        "您好👋 感谢您来帮助我们更好地了解老人的情况。\n\n"
        "作为老人的家属，您对他/她的日常生活最了解。"
        "我会通过一些问题来收集您的观察和建议，"
        "这样我们就能为老人提供更贴心的照护建议。\n\n"
        "请按照您的真实情况回答，没有标准答案。"
        "如果有些问题记不清，也没关系，大概说一下就可以。"
    )

    return {
        "session_id": session_id,
        "elderly_id": elderly_id,
        "greeting": greeting,
        "state": "GREETING",
    }


@family_router.post("/session/{session_id}/message")
async def send_family_message(request: Request, session_id: str, message: Dict[str, str]):
    """发送家属消息。"""
    family_manager = _get_family_manager(request)

    content = str(message.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="消息不能为空")

    try:
        return family_manager.chat(session_id, content)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"处理消息失败: {exc}") from exc


@family_router.get("/session/{session_id}/info")
async def get_family_session_info(request: Request, session_id: str):
    """获取家属会话信息。"""
    family_manager = _get_family_manager(request)

    try:
        return family_manager.get_session_info(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取会话信息失败: {exc}") from exc


@family_router.get("/elderly-list")
async def get_elderly_list(request: Request):
    """获取所有老年人列表（家属端）。"""
    conversation_manager = _get_conversation_manager(request)
    store = conversation_manager.store

    try:
        conn = sqlite3.connect(store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, profile, created_at, updated_at FROM users ORDER BY updated_at DESC")
        rows = cursor.fetchall()
        conn.close()

        elderly_list = []
        for row in rows:
            profile = json.loads(row["profile"]) if row["profile"] else {}
            elderly_list.append(
                {
                    "elderly_id": row["user_id"],
                    "name": profile.get("name", "未命名"),
                    "relation": "家庭成员",
                    "completion_rate": store.get_completion_rate(row["user_id"]),
                    "created_at": row["created_at"] or row["updated_at"] or datetime.now().isoformat(),
                }
            )

        return {"data": elderly_list}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取列表失败: {exc}") from exc


@family_router.get("/elderly/{elderly_id}")
async def get_elderly_detail(request: Request, elderly_id: str):
    """获取老年人详细信息。"""
    conversation_manager = _get_conversation_manager(request)
    profile = conversation_manager.store.get_profile(elderly_id)
    if not profile:
        raise HTTPException(status_code=404, detail="老年人不存在")

    return {
        "elderly_id": elderly_id,
        "profile": asdict(profile),
    }


@family_router.put("/elderly/{elderly_id}")
async def update_elderly_info(request: Request, elderly_id: str, updates: Dict[str, Any]):
    """更新老年人信息。"""
    conversation_manager = _get_conversation_manager(request)
    workspace_manager = _get_workspace_manager(request)
    store = conversation_manager.store

    if not store.user_exists(elderly_id):
        raise HTTPException(status_code=404, detail="老年人不存在")

    try:
        store.update_profile(elderly_id, updates)
        latest_session = store.get_latest_session(elderly_id)
        if latest_session is not None:
            workspace_manager.save_user_profile(
                latest_session["session_id"],
                asdict(store.get_profile(elderly_id)),
            )
            workspace_manager.update_metadata(latest_session["session_id"], {"has_profile": True})
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新失败: {exc}") from exc


@family_router.get("/reports/{elderly_id}")
async def get_elderly_reports(request: Request, elderly_id: str):
    """获取老年人的所有报告。"""
    conversation_manager = _get_conversation_manager(request)
    workspace_manager = _get_workspace_manager(request)

    if not conversation_manager.store.user_exists(elderly_id):
        raise HTTPException(status_code=404, detail="老年人不存在")

    try:
        reports = []
        for metadata in workspace_manager.find_sessions_by_user(elderly_id):
            session_id = metadata.get("session_id")
            if not session_id:
                continue

            for report_file in workspace_manager.get_report_files(session_id):
                with open(report_file, "r", encoding="utf-8") as file_obj:
                    content = json.load(file_obj)

                report_data = content.get("report_data") if isinstance(content, dict) else {}
                title = "健康评估报告"
                if isinstance(report_data, dict):
                    title = report_data.get("summary") or title

                reports.append(
                    {
                        "id": content.get("report_id", report_file.stem),
                        "title": title,
                        "created_at": content.get("generated_at")
                        or datetime.fromtimestamp(report_file.stat().st_mtime).isoformat(),
                        "content": content,
                    }
                )

        reports.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return {"data": reports}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取报告失败: {exc}") from exc
