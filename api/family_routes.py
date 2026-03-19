"""
家属端 API 路由
"""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import json

family_router = APIRouter(prefix="/family")

# 全局家属管理器（在 server.py 中初始化后设置）
family_manager = None


def set_family_manager(manager):
    """设置全局家属管理器"""
    global family_manager
    family_manager = manager


@family_router.post("/session/start/{elderly_id}")
async def start_family_session(elderly_id: str):
    """
    为老人启动家属端评估会话
    
    Args:
        elderly_id: 老人的 user_id
        
    Returns:
        {
            "session_id": "会话ID",
            "greeting": "欢迎语",
            "first_question": "第一个问题"
        }
    """
    if family_manager is None:
        raise HTTPException(status_code=500, detail="家属管理器未初始化")
    
    try:
        session_id = family_manager.new_family_session(elderly_id)
        
        greeting = (
            "您好👋 感谢您来帮助我们更好地了解老人的情况。\n\n"
            "作为老人的家属，您对他/她的日常生活最了解。"
            "我会通过一些问题来收集您的观察和建议，"
            "这样我们就能为老人提供更贴心的照护建议。\n\n"
            "请按照您的真实情况回答，没有标准答案。"
            "如果有些问题记不清，也没关系，大概说一下就可以。"
        )
        
        # 获取第一个问题
        session_info = family_manager.get_session_info(session_id)
        
        return {
            "session_id": session_id,
            "elderly_id": elderly_id,
            "greeting": greeting,
            "state": "GREETING"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动会话失败: {str(e)}")


@family_router.post("/session/{session_id}/message")
async def send_family_message(session_id: str, message: Dict[str, str]):
    """
    发送家属消息
    
    Args:
        session_id: 会话ID
        message: {"content": "消息内容"}
        
    Returns:
        {
            "reply": "AI回复",
            "state": "会话状态",
            "progress": 0.0-1.0,
            "collected_fields": ["已收集字段"],
            "missing_fields": ["缺失字段"]
        }
    """
    if family_manager is None:
        raise HTTPException(status_code=500, detail="家属管理器未初始化")
    
    try:
        content = message.get("content", "").strip()
        if not content:
            raise ValueError("消息不能为空")
        
        result = family_manager.chat(session_id, content)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"处理消息失败: {str(e)}")


@family_router.get("/session/{session_id}/info")
async def get_family_session_info(session_id: str):
    """获取家属会话信息"""
    if family_manager is None:
        raise HTTPException(status_code=500, detail="家属管理器未初始化")
    
    try:
        info = family_manager.get_session_info(session_id)
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话信息失败: {str(e)}")


@family_router.get("/elderly-list")
async def get_elderly_list():
    """获取所有老年人列表（家属端）"""
    if family_manager is None or family_manager.store is None:
        raise HTTPException(status_code=500, detail="管理器未初始化")
    
    try:
        import sqlite3
        conn = sqlite3.connect(family_manager.store.db_path)
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
                profile = json.loads(profile_json)
                elderly_list.append({
                    "elderly_id": user_id,
                    "name": profile.get("name", "未命名"),
                    "relation": "家庭成员",
                    "completion_rate": 0.8,
                    "created_at": profile.get("created_at", "")
                })
        
        return {"data": elderly_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取列表失败: {str(e)}")


@family_router.get("/elderly/{elderly_id}")
async def get_elderly_detail(elderly_id: str):
    """获取老年人详细信息"""
    if family_manager is None or family_manager.store is None:
        raise HTTPException(status_code=500, detail="管理器未初始化")
    
    try:
        profile = family_manager.store.get_profile(elderly_id)
        
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
    if family_manager is None or family_manager.store is None:
        raise HTTPException(status_code=500, detail="管理器未初始化")
    
    try:
        family_manager.store.update_profile(elderly_id, updates)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")
