"""
认证相关 API 路由。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from schemas import LoginRequest


auth_router = APIRouter(prefix="/auth")


@auth_router.post("/login")
async def login(request: LoginRequest):
    """简单的登录接口（演示用）。"""
    if not request.phone.strip() or not request.password.strip():
        raise HTTPException(status_code=400, detail="手机号和密码不能为空")

    token = f"token_{request.phone}_{datetime.now().timestamp()}"
    return {
        "token": token,
        "user_name": f"用户{request.phone[-4:]}",
        "role": "family",
    }


@auth_router.post("/logout")
async def logout():
    """登出接口。"""
    return {"success": True}
