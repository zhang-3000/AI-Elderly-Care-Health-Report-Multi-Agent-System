"""
对话管理器
支持自由对话与结构化题卡混合的评估流程。
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_agent_system_v2 import OrchestratorAgentV2
from memory.profile_extract_agent import ProfileExtractAgent
from memory.questionnaire import (
    BADL_OPTIONS,
    CHRONIC_BOOLEAN_FIELDS,
    FIELD_META,
    QUESTION_GROUPS,
    QUESTION_GROUP_MAP,
    STEP_TO_GROUP,
    YES_NO_OPTIONS,
    filter_chronic_items_by_sex,
)
from memory.user_profile_store import UserProfileStore


RAG_ENABLED = os.getenv("RAG_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


WELCOME_MESSAGE = (
    "您好呀，很高兴和您聊一聊。\n\n"
    "接下来我会用比较简单的问题，了解一下您最近的基本情况和身体状况，并为您整理一份更贴合您的健康小结和建议。\n\n"
    "您不用紧张，这不是考试，也没有标准答案。您按自己的实际情况慢慢说就可以；"
    "有些问题如果一时想不起来，也没关系，我会一步一步地陪您填写。\n\n"
    "那我们先从一些基本情况开始了解吧。"
)


class SessionState(str, Enum):
    GREETING = "GREETING"
    COLLECTING = "COLLECTING"
    CONFIRMING = "CONFIRMING"
    GENERATING = "GENERATING"
    REPORT_DONE = "REPORT_DONE"
    FOLLOW_UP = "FOLLOW_UP"


def _now_iso() -> str:
    return datetime.now().isoformat()


def _season_for_month(month: int) -> str:
    if month in {3, 4, 5}:
        return "春"
    if month in {6, 7, 8}:
        return "夏"
    if month in {9, 10, 11}:
        return "秋"
    return "冬"


class ConversationManager:
    def __init__(self, db_path: Optional[str] = None):
        self.store = UserProfileStore(db_path) if db_path else UserProfileStore()
        self.extractor = ProfileExtractAgent()
        self.orchestrator = OrchestratorAgentV2()
        self._session_cache: Dict[str, Dict[str, Any]] = {}

    # ─────────────────────────────────────────────
    # 用户 & 会话
    # ─────────────────────────────────────────────

    def new_user(self) -> str:
        return self.store.create_user()

    def new_session(self, user_id: str) -> str:
        if not self.store.user_exists(user_id):
            raise ValueError(f"用户 {user_id} 不存在，请先调用 new_user()")
        session_id = self.store.create_session(user_id)
        ctx = self._new_session_context(user_id)
        self._persist_ctx(session_id, ctx)
        return session_id

    def start_session(self, session_id: str) -> Dict[str, Any]:
        ctx = self._get_ctx(session_id)
        ctx["state"] = SessionState.COLLECTING.value
        interaction = self._find_next_interaction(ctx["user_id"], ctx)
        if interaction:
            ctx["current_group_id"] = interaction["groupId"]
            ctx["current_step_id"] = interaction["id"]
        self._persist_ctx(session_id, ctx)

        reply = WELCOME_MESSAGE
        if interaction:
            reply = f"{WELCOME_MESSAGE}\n\n{self._build_prompt_with_title(interaction)}"

        self.store.append_message(session_id, "assistant", reply)
        return self._build_response(
            session_id=session_id,
            reply=reply,
            state=SessionState.COLLECTING,
            interaction=interaction,
        )

    def resume_session(self, session_id: str, user_id: str):
        session = self.store.get_session(session_id)
        if session is None or session.get("user_id") != user_id:
            raise ValueError(f"会话 {session_id} 不存在")
        self._get_ctx(session_id)

    # ─────────────────────────────────────────────
    # 主入口
    # ─────────────────────────────────────────────

    def chat(
        self,
        session_id: str,
        user_message: str,
        answer: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ctx = self._get_ctx(session_id)
        user_id = ctx["user_id"]
        state = SessionState(ctx["state"])

        if state == SessionState.GREETING:
            return self.start_session(session_id)

        history = self.store.get_session_messages(session_id)

        if answer is None and user_message.strip():
            self.store.append_message(session_id, "user", user_message)
            history = self.store.get_session_messages(session_id)

        if state == SessionState.COLLECTING:
            reply, new_state, metadata = self._handle_collecting(
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
                answer=answer,
                history=history,
            )
        elif state == SessionState.CONFIRMING:
            reply, new_state, metadata = self._handle_confirming(
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
                answer=answer,
                history=history,
            )
        elif state == SessionState.REPORT_DONE:
            reply, new_state, metadata = self._handle_followup(
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
                history=history,
            )
        else:
            reply, new_state, metadata = ("系统处理中，请稍候...", state, {})

        ctx = self._get_ctx(session_id)
        ctx["state"] = new_state.value
        interaction = metadata.get("interaction")
        if interaction:
            ctx["current_group_id"] = interaction.get("groupId")
            ctx["current_step_id"] = interaction.get("id")
        else:
            ctx["current_group_id"] = None
            ctx["current_step_id"] = None
        self._persist_ctx(session_id, ctx)

        self.store.append_message(session_id, "assistant", reply)

        response = self._build_response(
            session_id=session_id,
            reply=reply,
            state=new_state,
            interaction=interaction,
        )
        response.update(metadata)
        return response

    # ─────────────────────────────────────────────
    # 状态处理
    # ─────────────────────────────────────────────

    def _handle_collecting(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        answer: Optional[Dict[str, Any]],
        history: List[Dict[str, Any]],
    ) -> Tuple[str, SessionState, Dict[str, Any]]:
        ctx = self._get_ctx(session_id)
        interaction = self._find_next_interaction(user_id, ctx)
        if interaction is None:
            return self._ready_to_confirm(user_id, session_id)

        if ctx.get("manual_edit_mode"):
            if answer:
                return "修改阶段请直接输入文字说明。", SessionState.COLLECTING, {"interaction": interaction}
            if not user_message.strip():
                return "请直接告诉我哪项信息需要修改。", SessionState.COLLECTING, {"interaction": interaction}
            updates = self.extractor.extract(user_message, list(FIELD_META.keys()), history[-8:])
            if not updates:
                return "我先没完全听明白，您可以直接说哪项要改成什么。", SessionState.COLLECTING, {"interaction": interaction}
            self.store.update_profile(user_id, updates)
            ctx["manual_edit_mode"] = False
            self._persist_ctx(session_id, ctx)
            return self._reply_for_next_step(user_id, session_id)

        if answer is not None:
            success, user_history_message, error_message = self._apply_structured_answer(
                user_id=user_id,
                session_id=session_id,
                interaction=interaction,
                answer=answer,
            )
            if not success:
                return error_message, SessionState.COLLECTING, {"interaction": interaction}
            if user_history_message:
                self.store.append_message(session_id, "user", user_history_message)
            return self._reply_for_next_step(user_id, session_id)

        if interaction["kind"] != "chat":
            coerced = self._coerce_text_answer(interaction, user_message)
            if coerced:
                success, user_history_message, error_message = self._apply_structured_answer(
                    user_id=user_id,
                    session_id=session_id,
                    interaction=interaction,
                    answer={"interactionId": interaction["id"], "values": coerced},
                )
                if success:
                    if history and history[-1]["role"] == "user" and history[-1]["content"] == user_message:
                        pass
                    else:
                        self.store.append_message(session_id, "user", user_history_message)
                    return self._reply_for_next_step(user_id, session_id)
                return error_message, SessionState.COLLECTING, {"interaction": interaction}
            return "这题我先给您做成选项，您直接点选会更方便。", SessionState.COLLECTING, {"interaction": interaction}

        updates = self._extract_chat_step(interaction, user_message, history)
        if not updates:
            return "我先没完全听明白，这部分您可以按刚才的问题再说得具体一点。", SessionState.COLLECTING, {"interaction": interaction}

        self._apply_profile_updates(user_id, interaction["id"], updates, session_id)
        return self._reply_for_next_step(user_id, session_id)

    def _handle_confirming(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        answer: Optional[Dict[str, Any]],
        history: List[Dict[str, Any]],
    ) -> Tuple[str, SessionState, Dict[str, Any]]:
        action = ""
        if answer:
            values = answer.get("values") or {}
            action = str(values.get("action") or "").strip().lower()
            if action:
                history_text = "确认生成报告" if action == "confirm" else "需要修改信息"
                self.store.append_message(session_id, "user", history_text)
        else:
            text = user_message.strip().lower()
            if any(token in text for token in ["确认", "开始", "生成", "好的", "可以", "没问题", "是"]):
                action = "confirm"
            elif any(token in text for token in ["修改", "不对", "重新", "改一下", "更正"]):
                action = "modify"

        if action == "modify":
            ctx = self._get_ctx(session_id)
            ctx["state"] = SessionState.COLLECTING.value
            ctx["manual_edit_mode"] = True
            self._persist_ctx(session_id, ctx)
            interaction = self._find_next_interaction(user_id, ctx)
            return (
                "好的，那您直接告诉我哪项信息需要修改，比如“高血压应该填没有”。",
                SessionState.COLLECTING,
                {"interaction": interaction},
            )

        if action == "confirm":
            return self._run_agent_workflow(user_id, session_id)

        interaction = self._build_confirm_interaction()
        return (
            "确认信息无误后我就开始生成报告；如果想改，直接选“修改信息”。",
            SessionState.CONFIRMING,
            {"interaction": interaction},
        )

    def _handle_followup(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Tuple[str, SessionState, Dict[str, Any]]:
        if any(token in user_message for token in ["重新", "再生成", "更新", "修改信息", "换一份"]):
            ctx = self._get_ctx(session_id)
            ctx["manual_edit_mode"] = True
            ctx["state"] = SessionState.COLLECTING.value
            self._persist_ctx(session_id, ctx)
            return (
                "好的，您直接告诉我哪里要修改，我改完后再生成。",
                SessionState.COLLECTING,
                {"interaction": self._find_next_interaction(user_id, ctx)},
            )

        reply = self._answer_followup(user_id, user_message, history)
        return reply, SessionState.REPORT_DONE, {"interaction": None}

    # ─────────────────────────────────────────────
    # 结构化题处理
    # ─────────────────────────────────────────────

    def _apply_structured_answer(
        self,
        user_id: str,
        session_id: str,
        interaction: Dict[str, Any],
        answer: Dict[str, Any],
    ) -> Tuple[bool, str, str]:
        interaction_id = str(answer.get("interactionId") or "").strip()
        if interaction_id and interaction_id != interaction["id"]:
            return False, "", "这张题卡已经更新了，请按当前页面重新作答。"

        values = answer.get("values")
        if not isinstance(values, dict):
            return False, "", "提交的答案格式不正确，请重新选择。"

        kind = interaction["kind"]
        if kind == "single_choice":
            field_name = interaction["field"]
            value = values.get(field_name)
            if value not in {option["value"] for option in interaction.get("options", [])}:
                return False, "", "请选择一个有效选项。"
            updates = {field_name: value}
            self._apply_profile_updates(user_id, interaction["id"], updates, session_id)
            return True, f"{interaction['prompt']} {value}", ""

        if kind == "matrix_single_choice":
            updates: Dict[str, Any] = {}
            valid_options = {option["value"] for option in interaction.get("options", [])}
            parts: List[str] = []
            for item in interaction.get("items", []):
                field_name = item["key"]
                value = values.get(field_name)
                if value not in valid_options:
                    return False, "", f"{item['label']} 还没有选择。"
                updates[field_name] = value
                parts.append(f"{item['label']}：{value}")
            self._apply_profile_updates(user_id, interaction["id"], updates, session_id)
            return True, "；".join(parts), ""

        if kind == "multi_select":
            selected = values.get("selected")
            if not isinstance(selected, list):
                return False, "", "多选答案格式不正确，请重新提交。"
            selected_set = {str(item) for item in selected}
            valid_keys = {item["key"] for item in interaction.get("items", [])}
            if not selected_set.issubset(valid_keys):
                return False, "", "存在无效选项，请重新选择。"
            updates = {}
            for item in interaction.get("items", []):
                key = item["key"]
                if key == "_other_chronic_note":
                    continue
                updates[key] = "是" if key in selected_set else "否"
            self._apply_profile_updates(user_id, interaction["id"], updates, session_id)
            ctx = self._get_ctx(session_id)
            ctx["needs_other_chronic_note"] = "_other_chronic_note" in selected_set
            self._persist_ctx(session_id, ctx)
            selected_labels = [item["label"] for item in interaction.get("items", []) if item["key"] in selected_set]
            return True, "已选择：" + ("、".join(selected_labels) if selected_labels else "无"), ""

        if kind == "form_card":
            updates = {}
            parts: List[str] = []
            for field in interaction.get("fields", []):
                field_name = field["key"]
                raw_value = values.get(field_name)
                if field.get("type") == "select":
                    valid_options = {option["value"] for option in field.get("options", [])}
                    if raw_value not in valid_options:
                        return False, "", f"{field['label']} 还没有选择。"
                    updates[field_name] = raw_value
                    parts.append(f"{field['label']}：{raw_value}")
                    continue

                if field.get("type") == "select_or_text":
                    valid_options = {option["value"] for option in field.get("options", [])}
                    if raw_value not in valid_options:
                        return False, "", f"{field['label']} 还没有选择。"
                    if raw_value == "其他":
                        custom_value = str(values.get(field.get("custom_key") or "") or "").strip()
                        if not custom_value:
                            return False, "", f"{field['label']} 请选择“其他”时需要补充说明。"
                        updates[field_name] = custom_value
                        parts.append(f"{field['label']}：{custom_value}")
                    else:
                        updates[field_name] = raw_value
                        parts.append(f"{field['label']}：{raw_value}")
                    continue

            self._apply_profile_updates(user_id, interaction["id"], updates, session_id)
            return True, "；".join(parts), ""

        if kind == "confirm":
            action = str(values.get("action") or "").strip().lower()
            if action not in {"confirm", "modify"}:
                return False, "", "请选择确认或修改。"
            return True, "确认生成报告" if action == "confirm" else "需要修改信息", ""

        return False, "", "当前题卡暂不支持提交。"

    def _coerce_text_answer(self, interaction: Dict[str, Any], user_message: str) -> Optional[Dict[str, Any]]:
        text = user_message.strip()
        if not text:
            return None

        if interaction["kind"] == "single_choice":
            for option in interaction.get("options", []):
                if option["value"] in text or option["label"] in text:
                    return {interaction["field"]: option["value"]}
            return None

        if interaction["kind"] == "confirm":
            lowered = text.lower()
            if any(token in lowered for token in ["确认", "开始", "生成", "好的", "可以"]):
                return {"action": "confirm"}
            if any(token in lowered for token in ["修改", "更正", "不对", "重新"]):
                return {"action": "modify"}
            return None

        return None

    # ─────────────────────────────────────────────
    # Chat 步骤处理
    # ─────────────────────────────────────────────

    def _extract_chat_step(
        self,
        interaction: Dict[str, Any],
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        step_id = interaction["id"]
        if step_id == "g7_time":
            return {"cognition_time": self._evaluate_day_answer(user_message)}
        if step_id == "g7_month":
            return {"cognition_month": self._evaluate_month_answer(user_message)}
        if step_id == "g7_season":
            return {"cognition_season": self._evaluate_season_answer(user_message)}
        if step_id == "g7_place":
            return {"cognition_place": self._evaluate_place_answer(user_message)}
        if step_id in {"g7_calc_1", "g7_calc_2", "g7_calc_3"}:
            return {"cognition_calc": [self._evaluate_calc_answer(step_id, user_message)]}

        target_fields = interaction.get("fields", [])
        return self.extractor.extract(user_message, target_fields, history[-8:])

    def _evaluate_day_answer(self, text: str) -> str:
        if self._is_unsure_text(text):
            return "不知道"
        day = datetime.now().day
        numbers = self._extract_numbers(text)
        return "正确" if day in numbers else "错误"

    def _evaluate_month_answer(self, text: str) -> str:
        if self._is_unsure_text(text):
            return "不知道"
        month = datetime.now().month
        numbers = self._extract_numbers(text)
        return "正确" if month in numbers else "错误"

    def _evaluate_season_answer(self, text: str) -> str:
        if self._is_unsure_text(text):
            return "不知道"
        season = _season_for_month(datetime.now().month)
        return "正确" if season in text else "错误"

    def _evaluate_place_answer(self, text: str) -> str:
        if self._is_unsure_text(text):
            return "不知道"
        normalized = text.strip()
        if any(token in normalized for token in ["家", "医院", "养老院", "社区", "卫生院", "诊所", "屋里"]):
            return "正确"
        return "错误" if normalized else "不知道"

    def _evaluate_calc_answer(self, step_id: str, text: str) -> str:
        if self._is_unsure_text(text):
            return "不知道"
        expected_map = {"g7_calc_1": 93, "g7_calc_2": 86, "g7_calc_3": 79}
        expected = expected_map[step_id]
        numbers = self._extract_numbers(text)
        return "正确" if expected in numbers else "错误"

    @staticmethod
    def _extract_numbers(text: str) -> List[int]:
        numbers: List[int] = []
        for match in re.findall(r"\d+", text):
            try:
                numbers.append(int(match))
            except ValueError:
                continue
        return numbers

    @staticmethod
    def _is_unsure_text(text: str) -> bool:
        lowered = text.strip().lower()
        return any(token in lowered for token in ["不知道", "不记得", "记不清", "忘了", "想不起来"])

    # ─────────────────────────────────────────────
    # 画像更新与跳题
    # ─────────────────────────────────────────────

    def _apply_profile_updates(
        self,
        user_id: str,
        interaction_id: str,
        updates: Dict[str, Any],
        session_id: str,
    ):
        ctx = self._get_ctx(session_id)
        if interaction_id in {"g7_calc_1", "g7_calc_2", "g7_calc_3"}:
            calc_values = ctx.get("calc_answers") or []
            if not isinstance(calc_values, list):
                calc_values = []
            new_value = (updates.get("cognition_calc") or ["错误"])[0]
            step_index = int(interaction_id.rsplit("_", 1)[-1]) - 1
            while len(calc_values) <= step_index:
                calc_values.append("")
            calc_values[step_index] = new_value
            ctx["calc_answers"] = calc_values[:3]
            self.store.update_session_context(session_id, self._serialize_ctx(ctx))
            if len(calc_values) >= 3 and all(item for item in calc_values[:3]):
                self.store.update_profile(user_id, {"cognition_calc": calc_values[:3]})
            return

        self.store.update_profile(user_id, updates)
        self._apply_followup_side_effects(user_id, interaction_id, updates, session_id)

    def _apply_followup_side_effects(
        self,
        user_id: str,
        interaction_id: str,
        updates: Dict[str, Any],
        session_id: str,
    ):
        ctx = self._get_ctx(session_id)

        if interaction_id == "g3_health_limitation":
            limitation = updates.get("health_limitation")
            if limitation == "完全没有影响":
                self.store.update_profile(
                    user_id,
                    {
                        "badl_bathing": "不需要帮助",
                        "badl_dressing": "不需要帮助",
                        "badl_toileting": "不需要帮助",
                        "badl_transferring": "不需要帮助",
                        "badl_continence": "不需要帮助",
                        "badl_eating": "不需要帮助",
                    },
                )
            elif limitation in {"有一点影响", "影响比较明显", "影响很大"}:
                self.store.update_profile(
                    user_id,
                    {
                        "badl_bathing": None,
                        "badl_dressing": None,
                        "badl_toileting": None,
                        "badl_transferring": None,
                        "badl_continence": None,
                        "badl_eating": None,
                    },
                    allow_none_overwrite=True,
                )

        if interaction_id == "g6_any":
            any_value = updates.get("chronic_disease_any")
            if any_value == "没有":
                reset_updates = {field: "否" for field in CHRONIC_BOOLEAN_FIELDS}
                reset_updates["cancer_type"] = None
                reset_updates["other_chronic_note"] = None
                self.store.update_profile(user_id, reset_updates, allow_none_overwrite=True)
                ctx["needs_other_chronic_note"] = False
            else:
                self.store.update_profile(
                    user_id,
                    {"cancer_type": None, "other_chronic_note": None},
                    allow_none_overwrite=True,
                )
                ctx["needs_other_chronic_note"] = False

        if interaction_id == "g6_detail":
            profile = asdict(self.store.get_profile(user_id))
            if profile.get("cancer") != "是":
                self.store.update_profile(user_id, {"cancer_type": None}, allow_none_overwrite=True)
            if not ctx.get("needs_other_chronic_note"):
                self.store.update_profile(user_id, {"other_chronic_note": None}, allow_none_overwrite=True)

        self._persist_ctx(session_id, ctx)

    # ─────────────────────────────────────────────
    # 下一步交互
    # ─────────────────────────────────────────────

    def _reply_for_next_step(
        self,
        user_id: str,
        session_id: str,
    ) -> Tuple[str, SessionState, Dict[str, Any]]:
        ctx = self._get_ctx(session_id)
        interaction = self._find_next_interaction(user_id, ctx)
        if interaction is None:
            return self._ready_to_confirm(user_id, session_id)
        return (
            self._build_prompt_with_title(interaction),
            SessionState.COLLECTING,
            {"interaction": interaction},
        )

    def _find_next_interaction(self, user_id: str, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if ctx.get("manual_edit_mode"):
            return {
                "id": "manual_edit",
                "groupId": "MANUAL_EDIT",
                "groupName": "信息修改",
                "kind": "chat",
                "prompt": "请直接告诉我哪项信息要改成什么。",
                "fields": list(FIELD_META.keys()),
                "allowFreeText": True,
                "submitLabel": "发送",
            }

        profile = self.store.get_profile(user_id)
        if profile is None:
            return None
        profile_dict = asdict(profile)

        for group in QUESTION_GROUPS:
            for step in group["steps"]:
                if not self._is_step_complete(group, step, profile_dict, ctx):
                    return self._build_interaction(group, step, profile_dict, ctx)
        return None

    def _is_step_complete(
        self,
        group: Dict[str, Any],
        step: Dict[str, Any],
        profile_dict: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> bool:
        step_id = step["id"]

        if step_id == "g6_detail":
            if profile_dict.get("chronic_disease_any") == "没有":
                return True
            if profile_dict.get("chronic_disease_any") not in {"有", "记不清"}:
                return False
            relevant_fields = self.store._required_group_fields(profile_dict, ctx).get(group["group_name"], [])
            required = [field for field in relevant_fields if field in CHRONIC_BOOLEAN_FIELDS]
            return all(self.store._is_field_filled(field, profile_dict.get(field)) for field in required)

        if step_id == "g6_cancer_type":
            if profile_dict.get("cancer") != "是":
                return True
            return self.store._is_field_filled("cancer_type", profile_dict.get("cancer_type"))

        if step_id == "g6_other_note":
            if not ctx.get("needs_other_chronic_note"):
                return True
            return self.store._is_field_filled("other_chronic_note", profile_dict.get("other_chronic_note"))

        if step_id.startswith("g7_calc_"):
            calc_answers = ctx.get("calc_answers") or []
            if not isinstance(calc_answers, list):
                return False
            expected_index = int(step_id.rsplit("_", 1)[-1]) - 1
            return len(calc_answers) > expected_index and bool(calc_answers[expected_index])

        if step["kind"] == "single_choice":
            field_name = step["field"]
            if step_id == "g3_health_limitation" and profile_dict.get(field_name) == "完全没有影响":
                return True
            return self.store._is_field_filled(field_name, profile_dict.get(field_name))

        if step["kind"] == "chat":
            relevant_fields = [
                field
                for field in step.get("fields", [])
                if self.store._is_field_applicable(field, profile_dict, ctx)
            ]
            return all(self.store._is_field_filled(field, profile_dict.get(field)) for field in relevant_fields)

        if step["kind"] in {"matrix_single_choice", "form_card"}:
            relevant_fields = [
                field
                for field in step.get("fields", [])
                if self.store._is_field_applicable(field, profile_dict, ctx)
            ]
            return all(self.store._is_field_filled(field, profile_dict.get(field)) for field in relevant_fields)

        return False

    def _build_interaction(
        self,
        group: Dict[str, Any],
        step: Dict[str, Any],
        profile_dict: Dict[str, Any],
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = step["prompt"]
        if step["kind"] == "chat" and len(step.get("fields", [])) > 1:
            missing_fields = [
                field
                for field in step["fields"]
                if self.store._is_field_applicable(field, profile_dict, ctx)
                and not self.store._is_field_filled(field, profile_dict.get(field))
            ]
            if missing_fields and len(missing_fields) < len(step["fields"]):
                prompt = self.extractor.generate_followup(missing_fields, group_question=prompt)

        interaction = {
            "id": step["id"],
            "groupId": group["group_id"],
            "groupName": group["group_name"],
            "kind": step["kind"],
            "prompt": prompt,
            "allowFreeText": step["kind"] == "chat",
            "submitLabel": "提交",
        }

        if step["kind"] == "single_choice":
            interaction["field"] = step["field"]
            interaction["options"] = step["options"]

        if step["kind"] == "matrix_single_choice":
            interaction["options"] = step["options"]
            interaction["items"] = step["items"]

        if step["kind"] == "multi_select":
            interaction["items"] = filter_chronic_items_by_sex(profile_dict.get("sex"))

        if step["kind"] == "form_card":
            missing_fields = {
                field
                for field in step["fields"]
                if self.store._is_field_applicable(field, profile_dict, ctx)
                and not self.store._is_field_filled(field, profile_dict.get(field))
            }
            interaction["fields"] = [
                field for field in step["form_fields"] if field["key"] in missing_fields
            ] or step["form_fields"]

        if step["kind"] == "chat":
            interaction["fields"] = step.get("fields", [])

        return interaction

    def _build_prompt_with_title(self, interaction: Dict[str, Any]) -> str:
        if interaction["groupId"] == "MANUAL_EDIT":
            return interaction["prompt"]
        group_idx = next(
            (index + 1 for index, group in enumerate(QUESTION_GROUPS) if group["group_id"] == interaction["groupId"]),
            0,
        )
        return f"**第{group_idx}部分：{interaction['groupName']}**\n\n{interaction['prompt']}"

    def _build_confirm_interaction(self) -> Dict[str, Any]:
        return {
            "id": "confirm_report",
            "groupId": "CONFIRM",
            "groupName": "确认生成",
            "kind": "confirm",
            "prompt": "如果信息都没问题，我就开始生成报告；如果还想改，先选修改。",
            "allowFreeText": True,
            "submitLabel": "确认",
            "options": [
                {"label": "确认生成报告", "value": "confirm"},
                {"label": "修改信息", "value": "modify"},
            ],
        }

    # ─────────────────────────────────────────────
    # 确认 / 报告
    # ─────────────────────────────────────────────

    def _ready_to_confirm(self, user_id: str, session_id: str) -> Tuple[str, SessionState, Dict[str, Any]]:
        profile = self.store.get_profile(user_id)
        progress = self.store.get_completion_rate(user_id, self._get_ctx(session_id))
        if profile is None:
            return "找不到用户信息，请重新开始。", SessionState.GREETING, {"interaction": None}

        summary_parts = []
        if profile.age:
            summary_parts.append(f"{profile.age}岁")
        if profile.sex:
            summary_parts.append(profile.sex)
        if profile.residence:
            summary_parts.append(profile.residence)
        if profile.health_limitation:
            summary_parts.append(f"健康限制：{profile.health_limitation}")

        chronic_labels = []
        chronic_map = {
            "hypertension": "高血压",
            "coronary_heart_disease": "冠心病",
            "stroke": "中风或脑血管疾病",
            "diabetes": "糖尿病",
            "cancer": "癌症或恶性肿瘤",
        }
        profile_dict = asdict(profile)
        for field_name, label in chronic_map.items():
            if profile_dict.get(field_name) == "是":
                chronic_labels.append(label)
        if chronic_labels:
            summary_parts.append("慢性病：" + "、".join(chronic_labels[:5]))

        reply = (
            f"好了，信息都收集完了！（完成度 {progress * 100:.0f}%）\n\n"
            f"目前整理到的摘要：{'；'.join(summary_parts) if summary_parts else '信息已整理完毕'}。\n\n"
            "确认信息无误后，我马上开始分析并生成健康评估与照护行动计划。"
        )
        return reply, SessionState.CONFIRMING, {"interaction": self._build_confirm_interaction()}

    def _run_agent_workflow(
        self,
        user_id: str,
        session_id: str,
    ) -> Tuple[str, SessionState, Dict[str, Any]]:
        profile = self.store.get_profile(user_id)
        if profile is None:
            return "找不到用户信息，请重新开始。", SessionState.GREETING, {"interaction": None}

        ctx = self._get_ctx(session_id)
        ctx["state"] = SessionState.GENERATING.value
        self._persist_ctx(session_id, ctx)
        self.store.update_session_status(session_id, "GENERATING")

        waiting_msg = (
            "好的，信息都收集齐了！现在开始帮您分析，大概需要1-2分钟，请稍候。\n\n"
            "正在进行：① 失能状态判定 → ② 风险预测 → ③ 健康画像 "
            f"{'→ ④ 知识检索 ' if RAG_ENABLED else ''}"
            "→ ⑤ 行动计划 → ⑥ 优先级排序 → ⑦ 报告生成..."
        )

        try:
            print(f"[ConversationManager] 开始为用户 {user_id} 生成报告...")
            results = self.orchestrator.run(profile, verbose=True)
            report_text = results.get("report", "报告生成失败，请重试。")
            self.store.update_session_status(session_id, "DONE")

            final_reply = (
                f"{waiting_msg}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{report_text}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "报告已生成完毕。如果您想修改信息后重新生成，直接告诉我就好。"
            )
            return (
                final_reply,
                SessionState.REPORT_DONE,
                {
                    "interaction": None,
                    "generated_report_results": results,
                    "generated_report_profile": asdict(profile),
                },
            )
        except Exception as exc:
            print(f"[ConversationManager] 工作流执行失败: {exc}")
            self.store.update_session_status(session_id, "COLLECTING")
            ctx = self._get_ctx(session_id)
            ctx["state"] = SessionState.CONFIRMING.value
            self._persist_ctx(session_id, ctx)
            return (
                f"抱歉，生成报告时遇到了一点问题（{str(exc)[:80]}）。请稍后再试。",
                SessionState.CONFIRMING,
                {"interaction": self._build_confirm_interaction()},
            )

    # ─────────────────────────────────────────────
    # 上下文 / 通用辅助
    # ─────────────────────────────────────────────

    def _new_session_context(self, user_id: str) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "state": SessionState.GREETING.value,
            "current_group_id": None,
            "current_step_id": None,
            "calc_answers": [],
            "manual_edit_mode": False,
            "needs_other_chronic_note": False,
        }

    def _serialize_ctx(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in ctx.items() if key != "user_id"}

    def _persist_ctx(self, session_id: str, ctx: Dict[str, Any]):
        self._session_cache[session_id] = ctx
        self.store.update_session_context(session_id, self._serialize_ctx(ctx))
        self.store.update_session_status(session_id, ctx["state"])

    def _get_ctx(self, session_id: str) -> Dict[str, Any]:
        cached = self._session_cache.get(session_id)
        if cached:
            return cached

        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(
                f"会话 {session_id} 不在内存中。请调用 resume_session() 恢复，或调用 new_session() 创建新会话。"
            )

        context = self.store.get_session_context(session_id)
        ctx = self._new_session_context(session["user_id"])
        ctx.update(context)
        if "state" not in ctx or not ctx["state"]:
            status = session.get("status") or SessionState.COLLECTING.value
            ctx["state"] = status
        self._session_cache[session_id] = ctx
        return ctx

    def _build_response(
        self,
        session_id: str,
        reply: str,
        state: SessionState,
        interaction: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        ctx = self._get_ctx(session_id)
        progress = self.store.get_completion_rate(ctx["user_id"], ctx)
        report_content = None
        if state == SessionState.REPORT_DONE:
            latest_session = self.store.get_latest_session(ctx["user_id"])
            if latest_session:
                messages = json.loads(latest_session.get("messages", "[]"))
                for message in reversed(messages):
                    if message["role"] != "assistant":
                        continue
                    content = message.get("content", "")
                    if (
                        "# 健康评估与照护行动计划" in content
                        or "# 健康评估与照顾行动计划" in content
                    ):
                        report_content = message["content"]
                        break

        return {
            "reply": reply,
            "state": state,
            "progress": progress,
            "report": report_content,
            "interaction": interaction,
            "profile_updates": {},
        }

    def _answer_followup(self, user_id: str, user_message: str, history: List[Dict[str, Any]]) -> str:
        from openai import OpenAI

        llm = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

        profile = self.store.get_profile(user_id)
        profile_summary = ""
        if profile:
            profile_summary = f"用户年龄：{profile.age}岁，性别：{profile.sex}"

        recent_history = [
            {"role": message["role"], "content": message["content"]}
            for message in history[-10:]
            if message["role"] in ("user", "assistant")
        ]

        system_msg = (
            "你是一个AI养老健康助手，刚刚为用户生成了一份健康评估与照护行动计划。"
            "现在用户有一些问题，请根据报告内容和用户信息，用口语化、亲切的方式回答。"
            f"用户基本信息：{profile_summary}"
        )

        try:
            response = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "system", "content": system_msg}] + recent_history,
                temperature=0.5,
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            return f"抱歉，回答出错了：{str(exc)[:50]}，请稍后再试。"

    # ─────────────────────────────────────────────
    # 便捷查询接口
    # ─────────────────────────────────────────────

    def get_progress(self, session_id: str) -> Dict[str, Any]:
        ctx = self._get_ctx(session_id)
        user_id = ctx["user_id"]
        missing = self.store.get_missing_fields(user_id, ctx)
        progress = self.store.get_completion_rate(user_id, ctx)

        completed_groups = [
            group["group_name"] for group in QUESTION_GROUPS if group["group_name"] not in missing
        ]
        pending_groups = [
            group["group_name"] for group in QUESTION_GROUPS if group["group_name"] in missing
        ]

        interaction = None
        if SessionState(ctx["state"]) == SessionState.CONFIRMING:
            interaction = self._build_confirm_interaction()
        elif SessionState(ctx["state"]) == SessionState.COLLECTING:
            interaction = self._find_next_interaction(user_id, ctx)

        return {
            "state": SessionState(ctx["state"]),
            "progress": progress,
            "completed_groups": completed_groups,
            "pending_groups": pending_groups,
            "missing_fields": missing,
            "interaction": interaction,
        }

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        return self.store.get_session_messages(session_id)

    def get_profile(self, session_id: str) -> Optional[Dict[str, Any]]:
        ctx = self._get_ctx(session_id)
        profile = self.store.get_profile(ctx["user_id"])
        return asdict(profile) if profile else None
