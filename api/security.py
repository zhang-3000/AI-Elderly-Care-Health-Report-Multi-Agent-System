"""
鉴权辅助函数。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from auth_service import DOCTOR_ROLE, ELDERLY_ROLE, FAMILY_ROLE, AuthActor


def require_state(request: Request, attr: str, error_message: str):
    manager = getattr(request.app.state, attr, None)
    if manager is None:
        raise HTTPException(status_code=500, detail=error_message)
    return manager


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "").strip()
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少有效的 Authorization Bearer token")
    token = header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少有效的 Authorization Bearer token")
    return token


def require_authenticated_actor(request: Request) -> AuthActor:
    auth_service = require_state(request, "auth_service", "认证服务未初始化")
    actor = auth_service.verify_access_token(_extract_bearer_token(request))
    if actor is None:
        raise HTTPException(status_code=401, detail="登录态无效或已过期")
    return actor


def require_elderly_actor(request: Request) -> AuthActor:
    actor = require_authenticated_actor(request)
    if actor.role != ELDERLY_ROLE:
        raise HTTPException(status_code=403, detail="仅老人本人可访问")
    return actor


def require_family_actor(request: Request) -> AuthActor:
    actor = require_authenticated_actor(request)
    if actor.role != FAMILY_ROLE:
        raise HTTPException(status_code=403, detail="仅家属账号可访问")
    return actor


def require_doctor_actor(request: Request) -> AuthActor:
    actor = require_authenticated_actor(request)
    if actor.role != DOCTOR_ROLE:
        raise HTTPException(status_code=403, detail="仅医生账号可访问")
    return actor


def _get_session_owner_user_id(request: Request, session_id: str) -> str:
    conversation_manager = require_state(request, "conversation_manager", "对话管理器未初始化")
    session = conversation_manager.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session["user_id"]


def ensure_actor_can_view_user(request: Request, elderly_user_id: str) -> AuthActor:
    actor = require_authenticated_actor(request)
    if actor.role == DOCTOR_ROLE:
        return actor
    if actor.role == ELDERLY_ROLE:
        if actor.subject_id != elderly_user_id:
            raise HTTPException(status_code=403, detail="无权访问该老年人数据")
        return actor

    auth_service = require_state(request, "auth_service", "认证服务未初始化")
    if not auth_service.check_family_access(actor.subject_id, elderly_user_id):
        raise HTTPException(status_code=403, detail="无权访问该老年人数据")
    return actor


def ensure_actor_can_view_session(request: Request, session_id: str) -> tuple[AuthActor, str]:
    owner_user_id = _get_session_owner_user_id(request, session_id)
    actor = ensure_actor_can_view_user(request, owner_user_id)
    return actor, owner_user_id


def ensure_actor_can_access_user(request: Request, elderly_user_id: str) -> AuthActor:
    actor = require_authenticated_actor(request)
    if actor.role == DOCTOR_ROLE:
        raise HTTPException(status_code=403, detail="医生账号仅支持只读访问")
    return ensure_actor_can_view_user(request, elderly_user_id)


def ensure_actor_can_access_session(request: Request, session_id: str) -> tuple[AuthActor, str]:
    actor = require_authenticated_actor(request)
    if actor.role == DOCTOR_ROLE:
        raise HTTPException(status_code=403, detail="医生账号仅支持只读访问")
    return ensure_actor_can_view_session(request, session_id)


def require_family_elderly_access(request: Request, elderly_user_id: str) -> AuthActor:
    actor = require_family_actor(request)
    auth_service = require_state(request, "auth_service", "认证服务未初始化")
    if not auth_service.check_family_access(actor.subject_id, elderly_user_id):
        raise HTTPException(status_code=403, detail="无权访问该老年人数据")
    return actor


def require_elderly_user_access(request: Request, elderly_user_id: str) -> AuthActor:
    actor = require_elderly_actor(request)
    if actor.subject_id != elderly_user_id:
        raise HTTPException(status_code=403, detail="仅老人本人可访问")
    return actor


def require_elderly_session_access(request: Request, session_id: str) -> tuple[AuthActor, str]:
    owner_user_id = _get_session_owner_user_id(request, session_id)
    actor = require_elderly_user_access(request, owner_user_id)
    return actor, owner_user_id


def require_family_session_access(request: Request, session_id: str) -> tuple[AuthActor, str]:
    owner_user_id = _get_session_owner_user_id(request, session_id)
    actor = require_family_elderly_access(request, owner_user_id)
    return actor, owner_user_id


def describe_actor(actor: AuthActor) -> dict[str, Any]:
    return {
        "subject_id": actor.subject_id,
        "role": actor.role,
        "expires_at": actor.expires_at,
    }
