from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import api.server as server
from memory.conversation_manager import ConversationManager


BACKEND_DIR = Path(__file__).resolve().parents[2]


def build_fake_workflow_results() -> dict:
    return {
        "status": {
            "status_description": "需要部分协助",
        },
        "risk": {
            "short_term_risks": [
                {
                    "risk": "跌倒",
                    "severity": "高",
                    "trigger": "步态不稳",
                    "prevention_key": "清理环境并加强陪护",
                    "timeframe": "1-4周",
                }
            ],
            "medium_term_risks": [
                {
                    "risk": "功能继续下降",
                    "severity": "中",
                    "chain": "活动量减少可能进一步削弱下肢力量",
                    "prevention_key": "规律训练和定期复评",
                    "timeframe": "1-6月",
                }
            ],
            "risk_summary": "存在跌倒与活动能力下降风险",
        },
        "factors": {
            "functional_status": {
                "description": "步态变慢，需要部分协助。",
            },
            "strengths": ["家属支持较好"],
            "main_problems": ["步态不稳"],
        },
        "actions": {
            "actions": [
                {
                    "action_id": "act_1",
                    "title": "整理居家环境",
                    "category": "安全管理",
                    "subtitle": "移除地面绊倒风险",
                    "completion_criteria": "本周内完成环境整理",
                }
            ]
        },
        "priority": {
            "priority_a": [
                {
                    "action_id": "act_1",
                    "reason": "先降低近期跌倒风险",
                }
            ],
            "priority_b": [],
            "priority_c": [],
        },
        "review": {
            "consistency_check": {
                "passed": False,
                "issues": ["风险等级与重点问题之间需要医生复核。"],
            },
            "safety_check": {
                "urgent": False,
                "urgent_reason": "",
            },
            "executability_check": {
                "passed": True,
                "issues": [],
            },
            "completeness_check": {
                "passed": True,
                "missing": [],
            },
            "suggestions": ["建议两周后复评步态。"],
            "overall_quality": "良",
            "approved": True,
        },
        "report": (
            "# 健康评估与照护行动计划\n\n"
            "## 0. 报告说明\n"
            "本报告基于当前信息生成，仅供参考。\n\n"
            "## 1. 健康报告总结\n"
            "整体情况需要持续观察。\n\n"
            "## 2. 您的健康画像\n"
            "建议加强安全管理。"
        ),
    }


class StubExtractor:
    def extract(
        self,
        user_message: str,
        target_fields: list[str],
        conversation_history: list[dict] | None = None,
    ) -> dict:
        message = user_message or ""
        updates: dict[str, object] = {}

        age_match = re.search(r"(\d{2,3})岁", message)
        if age_match and "age" in target_fields:
            updates["age"] = int(age_match.group(1))

        if "男" in message and "sex" in target_fields:
            updates["sex"] = "男"
        elif "女" in message and "sex" in target_fields:
            updates["sex"] = "女"

        province_map = {
            "北京": "北京",
            "上海": "上海",
            "广东": "广东",
            "河南": "河南",
        }
        for keyword, value in province_map.items():
            if keyword in message and "province" in target_fields:
                updates["province"] = value
                break

        if "城市" in message and "residence" in target_fields:
            updates["residence"] = "城市"
        elif "农村" in message and "residence" in target_fields:
            updates["residence"] = "农村"

        if any(keyword in message for keyword in ("小学", "6年", "读了6年")) and "education_years" in target_fields:
            updates["education_years"] = 6
        elif any(keyword in message for keyword in ("初中", "9年", "读了9年")) and "education_years" in target_fields:
            updates["education_years"] = 9

        if "已婚" in message and "marital_status" in target_fields:
            updates["marital_status"] = "已婚"
        elif "丧偶" in message and "marital_status" in target_fields:
            updates["marital_status"] = "丧偶"

        if "有一点" in message and "health_limitation" in target_fields:
            updates["health_limitation"] = "有一点"
        elif any(keyword in message for keyword in ("没有影响", "没影响")) and "health_limitation" in target_fields:
            updates["health_limitation"] = "没有"
        elif "比较严重" in message and "health_limitation" in target_fields:
            updates["health_limitation"] = "比较严重"

        chronic_fields = {
            "高血压": "hypertension",
            "糖尿病": "diabetes",
            "心脏病": "heart_disease",
            "中风": "stroke",
            "白内障": "cataract",
            "癌症": "cancer",
            "关节炎": "arthritis",
        }
        for keyword, field in chronic_fields.items():
            if field in target_fields and keyword in message:
                updates[field] = "是"

        return updates

    def generate_followup(
        self,
        missing_fields: list[str],
        conversation_history: list[dict] | None = None,
        group_question: str | None = None,
    ) -> str:
        if group_question:
            return group_question
        if not missing_fields:
            return "请继续。"
        return f"还需要补充：{'、'.join(missing_fields)}。"


class FlowConversationManager(ConversationManager):
    def __init__(self, db_path: str | None = None):
        super().__init__(db_path=db_path)
        self.extractor = StubExtractor()


class APIFlowTestCase(unittest.TestCase):
    doctor_name = "李医生"
    doctor_phone = "13900139000"
    doctor_password = "doctor123"

    def setUp(self):
        self.base_dir = BACKEND_DIR
        self.db_path = self.base_dir / "data" / "users.db"
        self.reports_dir = self.base_dir / "data" / "reports"
        self.workspace_dir = self.base_dir / "workspace"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        self.patches = [
            patch.object(server, "DB_PATH", str(self.db_path)),
            patch.object(server, "REPORTS_DIR", self.reports_dir),
            patch.object(server, "ConversationManager", FlowConversationManager),
            patch.dict(
                os.environ,
                {
                    "DOCTOR_DEFAULT_NAME": self.doctor_name,
                    "DOCTOR_DEFAULT_PHONE": self.doctor_phone,
                    "DOCTOR_DEFAULT_PASSWORD": self.doctor_password,
                },
                clear=False,
            ),
        ]

        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.client_context = TestClient(server.app)
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)

        self.conversation_manager = self.client.app.state.conversation_manager
        self.workspace_manager = self.client.app.state.workspace_manager

    def _unique_phone(self) -> str:
        return f"13{uuid.uuid4().int % 10**9:09d}"

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _start_chat(self) -> dict:
        response = self.client.post("/chat/start")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["userType"], "elderly")
        self.assertTrue(body["accessToken"])
        return body

    def _send_elderly_message(self, token: str, session_id: str, message: str) -> dict:
        response = self.client.post(
            "/chat/message",
            json={"message": message, "sessionId": session_id, "context": {}},
            headers=self._auth_headers(token),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _save_session_profile(self, token: str, session_id: str, profile: dict) -> dict:
        response = self.client.post(
            f"/api/sessions/{session_id}/profile",
            json=profile,
            headers=self._auth_headers(token),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _register_family(
        self,
        elderly_id: str,
        phone: str | None = None,
        password: str = "secret123",
        name: str = "张家属",
    ) -> tuple[dict, str, str]:
        phone = phone or self._unique_phone()
        response = self.client.post(
            "/auth/family/register",
            json={
                "name": name,
                "phone": phone,
                "password": password,
                "elderlyId": elderly_id,
                "relation": "子女",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), phone, password

    def _generate_report_with_session(
        self,
        session_id: str,
        token: str,
        profile: dict | None = None,
    ):
        request_payload = {
            "sessionId": session_id,
            "profile": profile
            or {
                "age": 82,
                "sex": "男",
                "residence": "城市",
                "education_years": 9,
            },
        }
        with patch.object(
            server,
            "_run_report_workflow",
            new=AsyncMock(return_value=build_fake_workflow_results()),
        ):
            return self.client.post(
                "/report/generate",
                json=request_payload,
                headers=self._auth_headers(token),
            )

    def _generate_report_for_elderly(
        self,
        elderly_id: str,
        token: str,
        payload: dict | None = None,
    ) -> dict:
        request_payload = payload or {
            "age": 84,
            "sex": "男",
            "residence": "农村",
            "education_years": 6,
        }
        with patch.object(
            server,
            "_run_report_workflow",
            new=AsyncMock(return_value=build_fake_workflow_results()),
        ):
            response = self.client.post(
                f"/report/generate/{elderly_id}",
                json=request_payload,
                headers=self._auth_headers(token),
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _login_doctor(self) -> dict:
        response = self.client.post(
            "/auth/login",
            json={
                "phone": self.doctor_phone,
                "password": self.doctor_password,
                "role": "doctor",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()
