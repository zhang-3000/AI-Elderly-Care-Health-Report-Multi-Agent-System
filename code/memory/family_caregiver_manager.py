"""
家属端对话管理器 - Family Caregiver Conversation Manager
专门为家属设计的评估流程
"""

import os
import sys
import json
from enum import Enum
from typing import Dict, Any, Optional, Tuple
from dataclasses import asdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.user_profile_store import UserProfileStore
from memory.family_questions import FAMILY_QUESTION_GROUPS, FAMILY_FIELD_META
from multi_agent_system_v2 import OrchestratorAgentV2


class FamilySessionState(str, Enum):
    """家属端会话状态"""
    GREETING = "GREETING"           # 初始问候
    COLLECTING = "COLLECTING"       # 收集信息中
    CONFIRMING = "CONFIRMING"       # 信息确认
    GENERATING = "GENERATING"       # 生成报告中
    REPORT_DONE = "REPORT_DONE"     # 报告完成
    FOLLOW_UP = "FOLLOW_UP"         # 后续答疑


class FamilyCaregiverManager:
    """
    家属端对话管理器
    
    特点：
    1. 主语从"您"改为"老人"，体现家属观察视角
    2. 关注"变化"而非"现状"
    3. 关注"照护支持"而非"自我评估"
    4. 支持三种场景：代答、补充、监测
    """

    def __init__(self, db_path: Optional[str] = None):
        self.store = UserProfileStore(db_path) if db_path else UserProfileStore()
        self.orchestrator = OrchestratorAgentV2()
        
        # 内存中维护每个会话的状态
        self._session_cache: Dict[str, Dict] = {}

    def new_family_session(self, elderly_id: str) -> str:
        """
        为老人创建新的家属端会话
        
        Args:
            elderly_id: 老人的 user_id
            
        Returns:
            session_id: 新会话 ID
        """
        if not self.store.user_exists(elderly_id):
            raise ValueError(f"老人 {elderly_id} 不存在")
        
        session_id = self.store.create_session(elderly_id)
        self._session_cache[session_id] = {
            "state": FamilySessionState.GREETING,
            "current_group_idx": 0,
            "elderly_id": elderly_id,
            "family_info": {},  # 存储家属基本信息
            "collected_fields": set(),
        }
        
        return session_id

    def chat(self, session_id: str, user_message: str) -> Dict[str, Any]:
        """
        处理家属的消息
        
        Args:
            session_id: 会话 ID
            user_message: 用户消息
            
        Returns:
            {
                "reply": "AI回复",
                "state": "当前状态",
                "progress": 0.0-1.0,
                "collected_fields": ["已收集的字段"],
                "missing_fields": ["缺失的字段"]
            }
        """
        if session_id not in self._session_cache:
            raise ValueError(f"会话 {session_id} 不存在")
        
        ctx = self._session_cache[session_id]
        elderly_id = ctx["elderly_id"]
        state = ctx["state"]
        
        # 获取对话历史
        history = self.store.get_session_messages(session_id)
        
        if state == FamilySessionState.GREETING:
            return self._handle_greeting(session_id, user_message, history)
        elif state == FamilySessionState.COLLECTING:
            return self._handle_collecting(session_id, user_message, history)
        elif state == FamilySessionState.CONFIRMING:
            return self._handle_confirming(session_id, user_message, history)
        else:
            return {
                "reply": "会话已结束",
                "state": state,
                "progress": 1.0
            }

    def _handle_greeting(self, session_id: str, user_message: str, history: list) -> Dict[str, Any]:
        """处理问候阶段"""
        ctx = self._session_cache[session_id]
        
        # 保存消息
        self.store.append_message(session_id, "user", user_message)
        
        # 移到收集阶段
        ctx["state"] = FamilySessionState.COLLECTING
        ctx["current_group_idx"] = 0
        
        # 生成第一个问题
        reply = self._build_question(ctx["current_group_idx"])
        
        self.store.append_message(session_id, "assistant", reply)
        
        return {
            "reply": reply,
            "state": FamilySessionState.COLLECTING,
            "progress": 0.0
        }

    def _handle_collecting(self, session_id: str, user_message: str, history: list) -> Dict[str, Any]:
        """处理信息收集阶段"""
        ctx = self._session_cache[session_id]
        elderly_id = ctx["elderly_id"]
        
        # 保存消息
        self.store.append_message(session_id, "user", user_message)
        
        # 提取信息（简化版，实际应该用 LLM）
        current_group = FAMILY_QUESTION_GROUPS[ctx["current_group_idx"]]
        for field in current_group.get("fields", []):
            ctx["collected_fields"].add(field)
        
        # 移到下一个问题
        ctx["current_group_idx"] += 1
        
        if ctx["current_group_idx"] >= len(FAMILY_QUESTION_GROUPS):
            # 所有问题都问完了
            ctx["state"] = FamilySessionState.CONFIRMING
            reply = "感谢您提供的所有信息！让我确认一下我理解的内容是否正确..."
        else:
            # 继续下一个问题
            reply = self._build_question(ctx["current_group_idx"])
        
        self.store.append_message(session_id, "assistant", reply)
        
        progress = ctx["current_group_idx"] / len(FAMILY_QUESTION_GROUPS)
        
        return {
            "reply": reply,
            "state": ctx["state"],
            "progress": progress,
            "collected_fields": list(ctx["collected_fields"]),
            "missing_fields": self._get_missing_fields(ctx)
        }

    def _handle_confirming(self, session_id: str, user_message: str, history: list) -> Dict[str, Any]:
        """处理确认阶段"""
        ctx = self._session_cache[session_id]
        
        # 保存消息
        self.store.append_message(session_id, "user", user_message)
        
        msg_lower = user_message.lower().strip()
        confirm_words = ["是", "对", "确认", "好", "好的", "可以", "没问题", "开始", "ok", "yes"]
        
        if any(w in msg_lower for w in confirm_words):
            # 用户确认，生成报告
            ctx["state"] = FamilySessionState.GENERATING
            reply = "正在为您生成照护建议报告..."
            self.store.append_message(session_id, "assistant", reply)
            
            return {
                "reply": reply,
                "state": FamilySessionState.GENERATING,
                "progress": 1.0
            }
        else:
            # 继续确认
            reply = "请确认信息是否正确。如果有需要修改的地方，请告诉我。"
            self.store.append_message(session_id, "assistant", reply)
            
            return {
                "reply": reply,
                "state": FamilySessionState.CONFIRMING,
                "progress": 0.95
            }

    def _build_question(self, group_idx: int) -> str:
        """构建问题"""
        if group_idx >= len(FAMILY_QUESTION_GROUPS):
            return "所有问题已完成"
        
        group = FAMILY_QUESTION_GROUPS[group_idx]
        group_name = group.get("group_name", "")
        
        # 简化版：直接返回问题
        if "question" in group:
            question = group["question"]
        elif "questions" in group:
            question = group["questions"][0].get("question", "")
        else:
            question = f"请告诉我关于{group_name}的信息"
        
        progress_hint = f"（第 {group_idx + 1}/{len(FAMILY_QUESTION_GROUPS)} 部分）\n\n"
        return progress_hint + question

    def _get_missing_fields(self, ctx: Dict) -> list:
        """获取缺失的字段"""
        all_fields = set()
        for group in FAMILY_QUESTION_GROUPS:
            all_fields.update(group.get("fields", []))
        
        return list(all_fields - ctx["collected_fields"])

    def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """获取会话信息"""
        if session_id not in self._session_cache:
            raise ValueError(f"会话 {session_id} 不存在")
        
        ctx = self._session_cache[session_id]
        
        return {
            "session_id": session_id,
            "elderly_id": ctx["elderly_id"],
            "state": ctx["state"],
            "progress": ctx["current_group_idx"] / len(FAMILY_QUESTION_GROUPS),
            "collected_fields": list(ctx["collected_fields"]),
            "missing_fields": self._get_missing_fields(ctx)
        }
