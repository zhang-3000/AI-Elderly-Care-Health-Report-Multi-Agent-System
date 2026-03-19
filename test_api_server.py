from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import api.server as server
from workspace_manager import WorkspaceManager


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
        "report": "## 1. 健康报告总结\n整体情况需要持续观察。\n\n## 2. 详细分析\n建议加强安全管理。",
    }


class APIServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        self.base_dir = Path(self.tempdir.name)
        self.db_path = self.base_dir / "users.db"
        self.reports_dir = self.base_dir / "reports"
        self.workspace_dir = self.base_dir / "workspace"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        workspace_dir = self.workspace_dir

        class TempWorkspaceManager(WorkspaceManager):
            def __init__(self, base_dir: str = "workspace"):
                super().__init__(base_dir=str(workspace_dir))

        self.patches = [
            patch.object(server, "DB_PATH", str(self.db_path)),
            patch.object(server, "REPORTS_DIR", self.reports_dir),
            patch.object(server, "WorkspaceManager", TempWorkspaceManager),
        ]

        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        self.client_context = TestClient(server.app)
        self.client = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)

        self.conversation_manager = self.client.app.state.conversation_manager
        self.workspace_manager = self.client.app.state.workspace_manager

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _start_chat(self) -> dict:
        response = self.client.post("/chat/start")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["userType"], "elderly")
        self.assertTrue(body["accessToken"])
        self.assertTrue(body["expiresAt"])
        return body

    def _register_family(
        self,
        elderly_id: str,
        phone: str = "13800138000",
        password: str = "secret123",
        name: str = "张家属",
    ) -> tuple[dict, str, str]:
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

    def _generate_report_for_elderly(
        self,
        elderly_id: str,
        token: str,
        payload: dict | None = None,
    ) -> dict:
        profile_payload = payload or {
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
                json=profile_payload,
                headers=self._auth_headers(token),
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

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

    def test_chat_start_issues_elderly_token_and_scopes_session_endpoints(self):
        start = self._start_chat()
        user_id = start["userId"]
        session_id = start["sessionId"]
        token = start["accessToken"]

        self.conversation_manager.store.update_profile(
            user_id,
            {
                "age": 82,
                "sex": "男",
                "province": "北京",
                "residence": "城市",
                "education_years": 9,
                "marital_status": "已婚",
            },
        )

        unauthorized = self.client.get(f"/chat/progress/{session_id}")
        self.assertEqual(unauthorized.status_code, 401)

        progress_response = self.client.get(
            f"/chat/progress/{session_id}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(progress_response.status_code, 200, progress_response.text)
        progress_body = progress_response.json()
        self.assertEqual(progress_body["state"], "collecting")
        self.assertIn("基本信息", progress_body["completedGroups"])

        profile_response = self.client.get(
            "/elderly/me/profile",
            headers=self._auth_headers(token),
        )
        self.assertEqual(profile_response.status_code, 200, profile_response.text)
        self.assertEqual(profile_response.json()["elderly_id"], user_id)

        sessions_response = self.client.get(
            "/api/sessions",
            headers=self._auth_headers(token),
        )
        self.assertEqual(sessions_response.status_code, 200, sessions_response.text)
        self.assertEqual(
            [item["session_id"] for item in sessions_response.json()["sessions"]],
            [session_id],
        )

        session_detail = self.client.get(
            f"/api/sessions/{session_id}",
            headers=self._auth_headers(token),
        )
        self.assertEqual(session_detail.status_code, 200, session_detail.text)
        self.assertEqual(session_detail.json()["metadata"]["user_id"], user_id)

    def test_elderly_cannot_access_other_elderly_session_or_report(self):
        elderly_one = self._start_chat()
        elderly_two = self._start_chat()

        denied_history = self.client.get(
            f"/chat/history/{elderly_two['sessionId']}",
            headers=self._auth_headers(elderly_one["accessToken"]),
        )
        self.assertEqual(denied_history.status_code, 403)

        report_response = self._generate_report_for_elderly(
            elderly_two["userId"],
            elderly_two["accessToken"],
        )
        report_id = report_response["reportId"]

        my_reports = self.client.get(
            "/elderly/me/reports",
            headers=self._auth_headers(elderly_two["accessToken"]),
        )
        self.assertEqual(my_reports.status_code, 200, my_reports.text)
        self.assertEqual([item["id"] for item in my_reports.json()["data"]], [report_id])

        own_report = self.client.get(
            f"/elderly/me/reports/{report_id}",
            headers=self._auth_headers(elderly_two["accessToken"]),
        )
        self.assertEqual(own_report.status_code, 200, own_report.text)
        self.assertEqual(own_report.json()["summary"], "整体情况需要持续观察。")

        other_report = self.client.get(
            f"/report/{report_id}",
            headers=self._auth_headers(elderly_one["accessToken"]),
        )
        self.assertEqual(other_report.status_code, 403)

        denied_me_report = self.client.get(
            f"/elderly/me/reports/{report_id}",
            headers=self._auth_headers(elderly_one["accessToken"]),
        )
        self.assertEqual(denied_me_report.status_code, 403)

    def test_family_register_bind_login_and_elderly_list_are_relation_scoped(self):
        elderly_one = self._start_chat()
        elderly_two = self._start_chat()
        elderly_three = self._start_chat()

        register_body, phone, password = self._register_family(elderly_one["userId"])
        family_token = register_body["token"]
        self.assertEqual(register_body["role"], "family")
        self.assertEqual(register_body["elderly_ids"], [elderly_one["userId"]])

        list_response = self.client.get(
            "/family/elderly-list",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(list_response.status_code, 200, list_response.text)
        self.assertEqual(
            {item["elderly_id"] for item in list_response.json()["data"]},
            {elderly_one["userId"]},
        )

        bind_response = self.client.post(
            "/auth/family/bind",
            json={
                "elderlyId": elderly_two["userId"],
                "relation": "配偶",
            },
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(bind_response.status_code, 200, bind_response.text)

        relisted = self.client.get(
            "/family/elderly-list",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(relisted.status_code, 200, relisted.text)
        self.assertEqual(
            {item["elderly_id"] for item in relisted.json()["data"]},
            {elderly_one["userId"], elderly_two["userId"]},
        )

        forbidden_detail = self.client.get(
            f"/family/elderly/{elderly_three['userId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(forbidden_detail.status_code, 403)

        login_response = self.client.post(
            "/auth/login",
            json={"phone": phone, "password": password},
        )
        self.assertEqual(login_response.status_code, 200, login_response.text)
        self.assertEqual(
            set(login_response.json()["elderly_ids"]),
            {elderly_one["userId"], elderly_two["userId"]},
        )

        logout_response = self.client.post("/auth/logout")
        self.assertEqual(logout_response.status_code, 200)
        self.assertEqual(logout_response.json(), {"success": True})

    def test_family_can_only_access_bound_reports_and_sessions(self):
        elderly_one = self._start_chat()
        elderly_two = self._start_chat()
        register_body, _, _ = self._register_family(
            elderly_one["userId"],
            phone="13800138001",
        )
        family_token = register_body["token"]

        report_one = self._generate_report_for_elderly(
            elderly_one["userId"],
            elderly_one["accessToken"],
        )
        report_two = self._generate_report_for_elderly(
            elderly_two["userId"],
            elderly_two["accessToken"],
        )
        self.assertNotEqual(report_one["reportId"], report_two["reportId"])

        sessions_response = self.client.get(
            "/api/sessions",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(sessions_response.status_code, 200, sessions_response.text)
        self.assertEqual(
            {item["user_id"] for item in sessions_response.json()["sessions"]},
            {elderly_one["userId"]},
        )

        allowed_session = self.client.get(
            f"/api/sessions/{report_one['sessionId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(allowed_session.status_code, 200, allowed_session.text)

        denied_session = self.client.get(
            f"/api/sessions/{report_two['sessionId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(denied_session.status_code, 403)

        family_reports = self.client.get(
            f"/family/reports/{elderly_one['userId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(family_reports.status_code, 200, family_reports.text)
        self.assertEqual(
            [item["id"] for item in family_reports.json()["data"]],
            [report_one["reportId"]],
        )

        forbidden_reports = self.client.get(
            f"/family/reports/{elderly_two['userId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(forbidden_reports.status_code, 403)

        shared_report = self.client.get(
            f"/report/{report_one['reportId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(shared_report.status_code, 200, shared_report.text)

        forbidden_report = self.client.get(
            f"/report/{report_two['reportId']}",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(forbidden_report.status_code, 403)

        export_allowed = self.client.get(
            f"/report/{report_one['reportId']}/export/pdf",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(export_allowed.status_code, 501)

        export_forbidden = self.client.get(
            f"/report/{report_two['reportId']}/export/pdf",
            headers=self._auth_headers(family_token),
        )
        self.assertEqual(export_forbidden.status_code, 403)

    def test_report_generation_requires_authenticated_bound_session(self):
        elderly = self._start_chat()

        unauthorized = self.client.post(
            "/report/generate",
            json={"profile": {"age": 80}, "sessionId": elderly["sessionId"]},
        )
        self.assertEqual(unauthorized.status_code, 401)

        missing_session = self._generate_report_with_session(
            session_id="",
            token=elderly["accessToken"],
        )
        self.assertEqual(missing_session.status_code, 400)

        success = self._generate_report_with_session(
            session_id=elderly["sessionId"],
            token=elderly["accessToken"],
        )
        self.assertEqual(success.status_code, 200, success.text)
        self.assertEqual(success.json()["summary"], "整体情况需要持续观察。")

        other = self._start_chat()
        cross_session = self._generate_report_with_session(
            session_id=other["sessionId"],
            token=elderly["accessToken"],
        )
        self.assertEqual(cross_session.status_code, 403)


if __name__ == "__main__":
    unittest.main()
