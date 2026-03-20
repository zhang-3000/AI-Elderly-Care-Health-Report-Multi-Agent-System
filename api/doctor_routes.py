"""
医生端只读聚合接口。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from report_utils import list_reports_for_user
from security import require_doctor_actor, require_state


doctor_router = APIRouter(prefix="/doctor")


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


def _sort_by_created_desc(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda item: item.get("created_at") or "", reverse=True)


def _choose_latest_timestamp(*values: str) -> str:
    normalized = [value for value in values if value]
    if not normalized:
        return ""
    return max(normalized)


def _load_user_row(db_path: str, elderly_id: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT user_id, profile, created_at, updated_at
            FROM users
            WHERE user_id = ?
            """,
            (elderly_id,),
        ).fetchone()
    finally:
        conn.close()


@doctor_router.get("/elderly-list")
async def get_doctor_elderly_list(request: Request):
    """返回医生可查看的老人总览。"""
    require_doctor_actor(request)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    store = conversation_manager.store

    summaries: List[Dict[str, Any]] = []
    for user_item in store.list_users():
        elderly_id = user_item["user_id"]
        row = _load_user_row(store.db_path, elderly_id)
        if row is None:
            continue

        profile = json.loads(row["profile"]) if row["profile"] else {}
        sessions = _sort_by_created_desc(workspace_manager.find_sessions_by_user(elderly_id))
        reports = list_reports_for_user(workspace_manager, elderly_id)

        latest_session = sessions[0] if sessions else {}
        latest_report = reports[0] if reports else {}
        updated_at = _choose_latest_timestamp(
            str(row["updated_at"] or ""),
            str(latest_session.get("created_at") or ""),
            str(latest_report.get("created_at") or ""),
        )
        summaries.append(
            {
                "elderly_id": elderly_id,
                "name": profile.get("name")
                or latest_session.get("title")
                or f"老人-{elderly_id[:8]}",
                "created_at": str(row["created_at"] or ""),
                "updated_at": updated_at,
                "has_profile": _has_profile_content(profile) or any(
                    bool(item.get("has_profile")) for item in sessions
                ),
                "has_report": bool(reports) or any(bool(item.get("has_report")) for item in sessions),
                "session_count": len(sessions),
                "report_count": len(reports),
                "latest_session_id": latest_session.get("session_id"),
                "latest_report_id": latest_report.get("id"),
            }
        )

    summaries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return {"data": summaries}


@doctor_router.get("/elderly/{elderly_id}")
async def get_doctor_elderly_detail(request: Request, elderly_id: str):
    """返回医生视角的单个老人详情。"""
    require_doctor_actor(request)
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    workspace_manager = require_state(request, "workspace_manager", "工作区管理器未初始化")
    store = conversation_manager.store

    row = _load_user_row(store.db_path, elderly_id)
    if row is None:
        raise HTTPException(status_code=404, detail="老年人不存在")

    profile = json.loads(row["profile"]) if row["profile"] else {}
    sessions = _sort_by_created_desc(workspace_manager.find_sessions_by_user(elderly_id))
    reports = list_reports_for_user(workspace_manager, elderly_id)

    return {
        "elderly_id": elderly_id,
        "name": profile.get("name") or (sessions[0].get("title") if sessions else None) or f"老人-{elderly_id[:8]}",
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "profile": profile,
        "sessions": sessions,
        "reports": reports,
    }
