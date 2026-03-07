"""
对话管理器
状态机 + 分组追问 + 自动触发 Agent 工作流
"""

import os
import sys
import json
from enum import Enum
from typing import Dict, Any, Optional, Tuple
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent_system_v2 import OrchestratorAgentV2, save_results
from memory.user_profile_store import UserProfileStore
from memory.profile_extract_agent import (
    ProfileExtractAgent,
    QUESTION_GROUPS,
    FIELD_TO_GROUP,
    FIELD_META,
)


RAG_ENABLED = os.getenv("RAG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


# ─────────────────────────────────────────────────────────────────────────────
# 会话状态枚举
# ─────────────────────────────────────────────────────────────────────────────

class SessionState(str, Enum):
    GREETING       = "GREETING"        # 初始打招呼
    COLLECTING     = "COLLECTING"      # 分组收集信息中
    CONFIRMING     = "CONFIRMING"      # 信息确认（全部填完，二次确认）
    GENERATING     = "GENERATING"      # 工作流执行中
    REPORT_DONE    = "REPORT_DONE"     # 报告生成完毕
    FOLLOW_UP      = "FOLLOW_UP"       # 报告后答疑


# ─────────────────────────────────────────────────────────────────────────────
# ConversationManager
# ─────────────────────────────────────────────────────────────────────────────

class ConversationManager:
    """
    对话管理器（核心入口）

    使用方式：
        manager = ConversationManager()
        user_id = manager.new_user()           # 或传入已有 user_id
        session_id = manager.new_session(user_id)
        response = manager.chat(session_id, "我妈妈今年82岁，女性，住在北京农村")
        print(response["reply"])               # 系统回复
        print(response["state"])               # 当前状态
        print(response["progress"])            # 完成度 0.0~1.0
    """

    def __init__(self, db_path: Optional[str] = None):
        self.store = UserProfileStore(db_path) if db_path else UserProfileStore()
        self.extractor = ProfileExtractAgent()
        self.orchestrator = OrchestratorAgentV2()

        # 内存中维护每个 session 的当前状态
        # session_id -> { "state": SessionState, "current_group_idx": int, "user_id": str }
        self._session_cache: Dict[str, Dict] = {}

    # ─────────────────────────────────────────────
    # 用户 & 会话创建
    # ─────────────────────────────────────────────

    def new_user(self) -> str:
        """创建新用户，返回 user_id"""
        return self.store.create_user()

    def new_session(self, user_id: str) -> str:
        """为用户创建新会话，返回 session_id，并发送欢迎语"""
        if not self.store.user_exists(user_id):
            raise ValueError(f"用户 {user_id} 不存在，请先调用 new_user()")
        session_id = self.store.create_session(user_id)
        self._session_cache[session_id] = {
            "state": SessionState.GREETING,
            "current_group_idx": 0,
            "user_id": user_id,
        }
        return session_id

    def resume_session(self, session_id: str, user_id: str):
        """恢复已有会话（服务重启后从数据库恢复状态）"""
        session = self.store.get_latest_session(user_id)
        if session is None:
            raise ValueError(f"会话 {session_id} 不存在")

        # 根据数据库中的状态恢复
        status = session.get("status", "COLLECTING")
        state = SessionState.COLLECTING if status != "DONE" else SessionState.REPORT_DONE

        # 计算当前应在哪个分组
        missing = self.store.get_missing_fields(user_id)
        current_group_idx = self._find_current_group_idx(missing)

        self._session_cache[session_id] = {
            "state": state,
            "current_group_idx": current_group_idx,
            "user_id": user_id,
        }

    # ─────────────────────────────────────────────
    # 主入口：处理用户消息
    # ─────────────────────────────────────────────

    def chat(self, session_id: str, user_message: str) -> Dict[str, Any]:
        """
        处理用户消息，返回系统回复

        Returns:
            {
                "reply": str,           # 系统回复文本
                "state": SessionState,  # 当前会话状态
                "progress": float,      # 画像完成度 0.0~1.0
                "report": str | None,   # 报告内容（仅 REPORT_DONE 时非空）
                "profile_updates": dict # 本轮新提取的字段（调试用）
            }
        """
        ctx = self._get_ctx(session_id)
        user_id = ctx["user_id"]
        state = ctx["state"]

        # 保存用户消息到历史
        self.store.append_message(session_id, "user", user_message)
        history = self.store.get_session_messages(session_id)

        # ── 状态路由 ──────────────────────────────
        if state == SessionState.GREETING:
            reply, new_state = self._handle_greeting(user_id, session_id, user_message, history)

        elif state == SessionState.COLLECTING:
            reply, new_state = self._handle_collecting(user_id, session_id, user_message, history)

        elif state == SessionState.CONFIRMING:
            reply, new_state = self._handle_confirming(user_id, session_id, user_message, history)

        elif state == SessionState.REPORT_DONE:
            reply, new_state = self._handle_followup(user_id, session_id, user_message, history)

        else:
            reply = "系统处理中，请稍候..."
            new_state = state

        # 更新状态
        ctx["state"] = new_state
        self._session_cache[session_id] = ctx

        # 保存系统回复到历史
        self.store.append_message(session_id, "assistant", reply)

        # 计算完成度
        progress = self.store.get_completion_rate(user_id)

        # 如果是报告完成，拿到报告内容
        report_content = None
        if new_state == SessionState.REPORT_DONE:
            latest_session = self.store.get_latest_session(user_id)
            if latest_session:
                msgs = json.loads(latest_session.get("messages", "[]"))
                # 找最后一条包含报告内容的 assistant 消息
                for msg in reversed(msgs):
                    if msg["role"] == "assistant" and "# 健康评估" in msg["content"]:
                        report_content = msg["content"]
                        break

        return {
            "reply": reply,
            "state": new_state,
            "progress": progress,
            "report": report_content,
            "profile_updates": {},  # 由各 handler 填充（可选）
        }

    # ─────────────────────────────────────────────
    # 状态处理器
    # ─────────────────────────────────────────────

    def _handle_greeting(
        self, user_id: str, session_id: str, user_message: str, history: list
    ) -> Tuple[str, SessionState]:
        """GREETING → 尝试从打招呼消息中提取信息，然后进入 COLLECTING"""

        # 先尝试提取 G1（基本信息）字段
        g1 = QUESTION_GROUPS[0]
        updates = self.extractor.extract(user_message, g1["fields"], history)
        if updates:
            self.store.update_profile(user_id, updates)

        # 进入 COLLECTING，问第一个有缺失的分组
        missing = self.store.get_missing_fields(user_id)
        group_idx = self._find_current_group_idx(missing)
        self._session_cache[session_id]["current_group_idx"] = group_idx

        if not missing:
            # 极少情况：打招呼时就说完了所有信息
            return self._ready_to_confirm(user_id)

        reply = self._build_greeting_reply(missing, group_idx)
        return reply, SessionState.COLLECTING

    def _handle_collecting(
        self, user_id: str, session_id: str, user_message: str, history: list
    ) -> Tuple[str, SessionState]:
        """COLLECTING → 提取字段 → 检查缺失 → 追问或进入确认"""

        ctx = self._session_cache[session_id]
        current_group_idx = ctx.get("current_group_idx", 0)

        # 获取当前分组需要提取的字段（包含当前组 + 可能的补充）
        target_fields = self._get_target_fields_for_extraction(user_id, current_group_idx)

        # 从用户回答中提取字段
        updates = self.extractor.extract(user_message, target_fields, history[-8:])
        if updates:
            self.store.update_profile(user_id, updates)

        # 重新检查缺失情况
        missing = self.store.get_missing_fields(user_id)

        if not missing:
            # 所有字段都填完了！进入确认阶段
            return self._ready_to_confirm(user_id)

        # 找到下一个需要追问的分组
        next_group_idx = self._find_current_group_idx(missing)
        ctx["current_group_idx"] = next_group_idx
        self._session_cache[session_id] = ctx

        # 生成追问
        reply = self._build_followup_reply(user_id, missing, next_group_idx, history)
        return reply, SessionState.COLLECTING

    def _handle_confirming(
        self, user_id: str, session_id: str, user_message: str, history: list
    ) -> Tuple[str, SessionState]:
        """CONFIRMING → 用户确认后触发 Agent 工作流"""

        msg_lower = user_message.lower().strip()
        # 检测用户是否确认
        confirm_words = ["是", "对", "确认", "好", "好的", "可以", "没问题", "开始", "ok", "yes", "生成", "出报告"]
        cancel_words = ["不", "修改", "不对", "错了", "重新", "改一下"]

        is_confirm = any(w in msg_lower for w in confirm_words)
        is_cancel = any(w in msg_lower for w in cancel_words)

        if is_cancel:
            # 用户要修改，回到收集阶段
            missing = self.store.get_missing_fields(user_id)
            group_idx = self._find_current_group_idx(missing) if missing else 0
            self._session_cache[session_id]["current_group_idx"] = group_idx
            reply = (
                "好的，那您想修改哪部分信息呢？直接告诉我就好，"
                "比如'血压那里填错了，应该是有高血压'。"
            )
            return reply, SessionState.COLLECTING

        if is_confirm or len(msg_lower) < 20:
            # 用户确认，开始生成报告
            return self._run_agent_workflow(user_id, session_id)

        # 不确定，再问一次
        reply = "您是确认信息都填好了，要生成报告吗？说'确认'就开始，说'修改'可以改一改。"
        return reply, SessionState.CONFIRMING

    def _handle_followup(
        self, user_id: str, session_id: str, user_message: str, history: list
    ) -> Tuple[str, SessionState]:
        """REPORT_DONE → 报告后的答疑（可重新收集信息再生成）"""

        # 检测是否要重新生成
        regen_words = ["重新", "再生成", "更新", "修改信息", "换一份"]
        if any(w in user_message for w in regen_words):
            return (
                "好的，您可以告诉我哪里要修改，我来重新生成报告。",
                SessionState.COLLECTING
            )

        # 其他答疑：用 LLM 直接回答
        reply = self._answer_followup(user_id, user_message, history)
        return reply, SessionState.REPORT_DONE

    # ─────────────────────────────────────────────
    # Agent 工作流触发
    # ─────────────────────────────────────────────

    def _run_agent_workflow(
        self, user_id: str, session_id: str
    ) -> Tuple[str, SessionState]:
        """触发 OrchestratorAgentV2 生成报告"""

        profile = self.store.get_profile(user_id)
        if profile is None:
            return "找不到用户信息，请重新开始。", SessionState.GREETING

        # 更新会话状态
        self.store.update_session_status(session_id, "GENERATING")

        # 提示用户等待
        waiting_msg = (
            "好的，信息都收集齐了！现在开始帮您分析，大概需要1-2分钟，请稍候⏳\n\n"
            "正在进行：① 失能状态判定 → ② 风险预测 → ③ 健康画像 "
            f"{'→ ④ 知识检索 ' if RAG_ENABLED else ''}"
            "→ ⑤ 行动计划 → ⑥ 优先级排序 → ⑦ 报告生成..."
        )

        try:
            print(f"[ConversationManager] 开始为用户 {user_id} 生成报告...")
            results = self.orchestrator.run(profile, verbose=True)

            report_text = results.get("report", "报告生成失败，请重试。")

            # 保存结果到文件（可选）
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data", "output_chat"
            )
            try:
                save_results(results, profile, output_dir=output_dir)
            except Exception as e:
                print(f"[ConversationManager] 保存文件失败（不影响主流程）: {e}")

            # 更新会话状态为完成
            self.store.update_session_status(session_id, "DONE")

            final_reply = (
                f"{waiting_msg}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{report_text}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "报告已生成完毕✅ 如果有任何疑问，或者想修改某项信息重新生成，"
                "直接告诉我就好😊"
            )
            return final_reply, SessionState.REPORT_DONE

        except Exception as e:
            print(f"[ConversationManager] 工作流执行失败: {e}")
            import traceback
            traceback.print_exc()
            self.store.update_session_status(session_id, "COLLECTING")
            return (
                f"抱歉，生成报告时遇到了一点问题（{str(e)[:50]}）。"
                "请稍后再试，或者说'重试'重新生成。",
                SessionState.COLLECTING
            )

    # ─────────────────────────────────────────────
    # 辅助方法
    # ─────────────────────────────────────────────

    def _get_ctx(self, session_id: str) -> Dict:
        """获取会话上下文，如果不在内存中则尝试从数据库恢复"""
        if session_id not in self._session_cache:
            # 尝试从数据库恢复会话
            try:
                session = self.store.get_session(session_id)
                if session:
                    user_id = session.get("user_id")
                    if user_id and self.store.user_exists(user_id):
                        # 直接恢复该会话到内存（不使用 resume_session，因为它会获取最新会话）
                        status = session.get("status", "COLLECTING")
                        state = SessionState.COLLECTING if status != "DONE" else SessionState.REPORT_DONE

                        # 计算当前应在哪个分组
                        missing = self.store.get_missing_fields(user_id)
                        current_group_idx = self._find_current_group_idx(missing)

                        self._session_cache[session_id] = {
                            "state": state,
                            "current_group_idx": current_group_idx,
                            "user_id": user_id,
                        }
                        return self._session_cache[session_id]
            except Exception as e:
                # 恢复失败，记录错误并抛出原始错误
                print(f"Error restoring session {session_id}: {e}")
                pass

            raise ValueError(
                f"会话 {session_id} 不在内存中。请调用 resume_session() 恢复，"
                "或调用 new_session() 创建新会话。"
            )
        return self._session_cache[session_id]

    def _find_current_group_idx(self, missing: Dict[str, list]) -> int:
        """根据缺失字段找到第一个有缺失的分组索引"""
        missing_field_set = set()
        for fields in missing.values():
            missing_field_set.update(fields)

        for i, group in enumerate(QUESTION_GROUPS):
            for f in group["fields"]:
                if f in missing_field_set:
                    return i
        return 0

    def _get_target_fields_for_extraction(self, user_id: str, group_idx: int) -> list:
        """
        获取当前轮次需要提取的字段
        主要提取当前分组的字段，但也包含之前分组中遗漏的字段（防止用户一次性说了很多）
        """
        missing = self.store.get_missing_fields(user_id)
        missing_field_set = set()
        for fields in missing.values():
            missing_field_set.update(fields)

        # 当前分组字段
        current_group = QUESTION_GROUPS[group_idx]
        target = list(current_group["fields"])

        # 加入当前分组之前遗漏的字段（最多额外加5个）
        extra_count = 0
        for i in range(group_idx):
            for f in QUESTION_GROUPS[i]["fields"]:
                if f in missing_field_set and f not in target:
                    target.append(f)
                    extra_count += 1
                    if extra_count >= 5:
                        break
            if extra_count >= 5:
                break

        return target

    def _ready_to_confirm(self, user_id: str) -> Tuple[str, SessionState]:
        """所有字段收集完毕，生成确认消息"""
        profile = self.store.get_profile(user_id)
        progress = self.store.get_completion_rate(user_id)

        # 生成信息摘要
        summary_lines = []
        if profile.age:
            summary_lines.append(f"年龄：{profile.age}岁")
        if profile.sex:
            summary_lines.append(f"性别：{profile.sex}")
        if profile.residence:
            summary_lines.append(f"居住地：{profile.residence}")

        diseases = []
        for field, name in [
            ("hypertension", "高血压"), ("diabetes", "糖尿病"),
            ("heart_disease", "心脏病"), ("stroke", "中风"),
            ("cataract", "白内障"), ("cancer", "癌症"), ("arthritis", "关节炎")
        ]:
            if getattr(profile, field) == "是":
                diseases.append(name)
        if diseases:
            summary_lines.append(f"慢性病：{'、'.join(diseases)}")
        elif any(getattr(profile, f) == "否" for f in ["hypertension", "diabetes"]):
            summary_lines.append("慢性病：无明显慢性病")

        summary = "、".join(summary_lines) if summary_lines else "信息已收集完毕"

        reply = (
            f"好了，信息都收集完了！（完成度 {progress*100:.0f}%）\n\n"
            f"📋 基本摘要：{summary}\n\n"
            "确认信息无误后，我马上开始分析并生成健康评估报告。\n"
            "**确认生成报告吗？**（说'确认'或'好的'就开始，说'修改'可以改改）"
        )
        return reply, SessionState.CONFIRMING

    def _build_greeting_reply(self, missing: Dict[str, list], group_idx: int) -> str:
        """构造初始欢迎语 + 第一组问题"""
        first_group = QUESTION_GROUPS[group_idx]
        total_groups = len(QUESTION_GROUPS)

        greeting = (
            "您好！我是AI养老健康助手😊\n\n"
            "为了给您（或家中老人）生成一份个性化的健康评估报告，"
            f"我需要了解一些基本情况，一共分{total_groups}个部分，"
            "每部分几个问题，您可以用自己的话来回答，不用太正式。\n\n"
            f"**第1部分：{first_group['group_name']}**\n\n"
            f"{first_group['question']}"
        )
        return greeting

    def _build_followup_reply(
        self, user_id: str, missing: Dict[str, list],
        group_idx: int, history: list
    ) -> str:
        """构造追问话术"""
        group = QUESTION_GROUPS[group_idx]
        total_filled = int(self.store.get_completion_rate(user_id) * len(FIELD_META))
        total_fields = len(FIELD_META)

        # 计算已完成几个分组
        completed_groups = sum(
            1 for g in QUESTION_GROUPS
            if g["group_name"] not in missing
        )
        total_groups = len(QUESTION_GROUPS)

        progress_hint = (
            f"（进度：{completed_groups}/{total_groups} 部分完成，"
            f"字段完成率 {self.store.get_completion_rate(user_id)*100:.0f}%）\n\n"
        )

        # 检查这个分组是否有部分字段已填，只问缺的那些
        group_missing = [f for f in group["fields"] if f in (missing.get(group["group_name"], []))]

        if len(group_missing) == len(group["fields"]):
            # 整个分组都是空的，用完整问题模板
            question = group["question"]
        else:
            # 只有部分字段缺失，动态生成追问
            question = self.extractor.generate_followup(
                missing_fields=group_missing,
                conversation_history=history[-6:],
            )

        group_title = f"**第{group_idx+1}部分：{group['group_name']}**\n\n"
        return progress_hint + group_title + question

    def _answer_followup(self, user_id: str, user_message: str, history: list) -> str:
        """报告生成后的答疑"""
        from openai import OpenAI
        llm = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        )

        profile = self.store.get_profile(user_id)
        profile_summary = ""
        if profile:
            profile_summary = f"用户年龄：{profile.age}岁，性别：{profile.sex}"

        recent_history = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-10:]
            if m["role"] in ("user", "assistant")
        ]

        system_msg = (
            "你是一个AI养老健康助手，刚刚为用户生成了一份健康评估报告。"
            "现在用户有一些问题，请根据报告内容和用户信息，用口语化、亲切的方式回答。"
            f"用户基本信息：{profile_summary}"
        )

        messages = [{"role": "system", "content": system_msg}] + recent_history

        try:
            response = llm.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                temperature=0.5,
                max_tokens=500
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"抱歉，回答出错了：{str(e)[:50]}，请稍后再试。"

    # ─────────────────────────────────────────────
    # 便捷查询接口
    # ─────────────────────────────────────────────

    def get_progress(self, session_id: str) -> Dict[str, Any]:
        """获取当前进度信息（用于 Web UI 展示进度条）"""
        ctx = self._get_ctx(session_id)
        user_id = ctx["user_id"]
        missing = self.store.get_missing_fields(user_id)
        progress = self.store.get_completion_rate(user_id)

        completed_groups = [
            g["group_name"] for g in QUESTION_GROUPS
            if g["group_name"] not in missing
        ]
        pending_groups = [
            g["group_name"] for g in QUESTION_GROUPS
            if g["group_name"] in missing
        ]

        return {
            "state": ctx["state"],
            "progress": progress,
            "completed_groups": completed_groups,
            "pending_groups": pending_groups,
            "missing_fields": missing,
        }

    def get_history(self, session_id: str) -> list:
        """获取当前会话的完整对话历史"""
        return self.store.get_session_messages(session_id)

    def get_profile(self, session_id: str) -> Optional[Dict]:
        """获取当前用户画像（dict 格式）"""
        ctx = self._get_ctx(session_id)
        profile = self.store.get_profile(ctx["user_id"])
        return asdict(profile) if profile else None
