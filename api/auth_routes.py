"""
认证相关 API 路由。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from auth_service import DOCTOR_ROLE, FAMILY_ROLE
from schemas import AuthResponse, FamilyBindRequest, FamilyRegisterRequest, LoginRequest
from security import require_family_actor, require_state


auth_router = APIRouter(prefix="/auth")


@auth_router.post("/family/register", response_model=AuthResponse)
async def register_family(request: Request, payload: FamilyRegisterRequest) -> AuthResponse:
    """注册家属账号并绑定首位老人。"""
    auth_service = require_state(request, "auth_service", "认证服务未初始化")

    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if not payload.phone.strip() or not payload.password.strip():
        raise HTTPException(status_code=400, detail="手机号和密码不能为空")

    success, message, data = auth_service.register_family(
        name=payload.name.strip(),
        phone=payload.phone.strip(),
        password=payload.password.strip(),
        elderly_user_id=payload.elderlyId.strip(),
        relation=payload.relation.strip() or "家属",
    )
    if not success or data is None:
        status_code = 404 if "老年人不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message)
    return AuthResponse(**data)


@auth_router.post("/family/bind")
async def bind_family_to_elderly(request: Request, payload: FamilyBindRequest):
    """为当前家属账号补充绑定新的老人。"""
    actor = require_family_actor(request)
    auth_service = require_state(request, "auth_service", "认证服务未初始化")

    success, message = auth_service.bind_family_to_elderly(
        family_id=actor.subject_id,
        elderly_user_id=payload.elderlyId.strip(),
        relation=payload.relation.strip() or "家属",
    )
    if not success:
        status_code = 404 if "老年人不存在" in message else 400
        raise HTTPException(status_code=status_code, detail=message)
    return {"success": True}


@auth_router.post("/login", response_model=AuthResponse)
async def login(request: Request, payload: LoginRequest) -> AuthResponse:
    """按角色执行真实登录。"""
    auth_service = require_state(request, "auth_service", "认证服务未初始化")
    if not payload.phone.strip() or not payload.password.strip():
        raise HTTPException(status_code=400, detail="手机号和密码不能为空")

    role = (payload.role or FAMILY_ROLE).strip().lower()
    if role == FAMILY_ROLE:
        success, message, data = auth_service.authenticate_family(
            payload.phone.strip(),
            payload.password.strip(),
        )
    elif role == DOCTOR_ROLE:
        success, message, data = auth_service.authenticate_doctor(
            payload.phone.strip(),
            payload.password.strip(),
        )
    else:
        raise HTTPException(status_code=400, detail="不支持的登录角色")

    if not success or data is None:
        raise HTTPException(status_code=401, detail=message)
    return AuthResponse(**data)


@auth_router.post("/logout")
async def logout():
    """登出接口。"""
    return {"success": True}
