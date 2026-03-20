from __future__ import annotations

import unittest

from tests.api_flows.support import APIFlowTestCase


class ElderlyApiFlowTestCase(APIFlowTestCase):
    def test_elderly_full_api_flow(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(health.json()["status"], "healthy")

        start = self._start_chat()
        session_id = start["sessionId"]
        elderly_id = start["userId"]
        elderly_token = start["accessToken"]

        first_message = self._send_elderly_message(
            elderly_token,
            session_id,
            "我82岁，男，北京，城市，小学毕业，已婚。",
        )
        self.assertEqual(first_message["state"], "collecting")
        self.assertGreater(first_message["progress"], 0)
        self.assertFalse(first_message["completed"])

        second_message = self._send_elderly_message(
            elderly_token,
            session_id,
            "这半年有一点影响，最近出门更吃力了。",
        )
        self.assertEqual(second_message["state"], "collecting")

        history = self.client.get(
            f"/chat/history/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(history.status_code, 200, history.text)
        self.assertGreaterEqual(len(history.json()), 6)

        progress = self.client.get(
            f"/chat/progress/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(progress.status_code, 200, progress.text)
        progress_body = progress.json()
        self.assertEqual(progress_body["state"], "collecting")
        self.assertIn("基本信息", progress_body["completedGroups"])
        self.assertIn("健康限制", progress_body["completedGroups"])

        profile = self.client.get(
            f"/chat/profile/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        profile_body = profile.json()
        self.assertEqual(profile_body["age"], 82)
        self.assertEqual(profile_body["sex"], "男")
        self.assertEqual(profile_body["health_limitation"], "有一点")

        saved = self._save_session_profile(
            elderly_token,
            session_id,
            {
                "age": 82,
                "sex": "男",
                "province": "北京",
                "residence": "城市",
                "education_years": 6,
                "marital_status": "已婚",
                "hypertension": "是",
                "diabetes": "是",
                "living_arrangement": "与子女同住",
            },
        )
        self.assertEqual(saved, {"success": True})

        report_response = self._generate_report_with_session(
            session_id=session_id,
            token=elderly_token,
            profile={
                "age": 82,
                "sex": "男",
                "residence": "城市",
                "education_years": 6,
                "hypertension": "是",
                "diabetes": "是",
            },
        )
        self.assertEqual(report_response.status_code, 200, report_response.text)
        report_body = report_response.json()
        self.assertEqual(report_body["summary"], "整体情况需要持续观察。")

        sessions = self.client.get(
            "/api/sessions",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(sessions.status_code, 200, sessions.text)
        self.assertEqual([item["session_id"] for item in sessions.json()["sessions"]], [session_id])

        session_detail = self.client.get(
            f"/api/sessions/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(session_detail.status_code, 200, session_detail.text)
        detail_body = session_detail.json()
        self.assertEqual(detail_body["metadata"]["user_id"], elderly_id)
        self.assertTrue(detail_body["metadata"]["has_profile"])
        self.assertEqual(len(detail_body["reports"]), 1)
        report_id = detail_body["reports"][0]["report_id"]

        shared_report = self.client.get(
            f"/report/{report_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(shared_report.status_code, 200, shared_report.text)
        self.assertEqual(shared_report.json()["summary"], "整体情况需要持续观察。")

        elderly_profile = self.client.get(
            "/elderly/me/profile",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(elderly_profile.status_code, 200, elderly_profile.text)
        self.assertEqual(elderly_profile.json()["elderly_id"], elderly_id)

        elderly_reports = self.client.get(
            "/elderly/me/reports",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(elderly_reports.status_code, 200, elderly_reports.text)
        self.assertEqual([item["id"] for item in elderly_reports.json()["data"]], [report_id])

        elderly_report_detail = self.client.get(
            f"/elderly/me/reports/{report_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(elderly_report_detail.status_code, 200, elderly_report_detail.text)
        self.assertEqual(elderly_report_detail.json()["summary"], "整体情况需要持续观察。")

        export_response = self.client.get(
            f"/report/{report_id}/export/pdf",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(export_response.status_code, 501)

    def test_elderly_can_delete_own_session_workspace(self):
        start = self._start_chat()
        session_id = start["sessionId"]
        elderly_token = start["accessToken"]

        response = self.client.delete(
            f"/api/sessions/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"success": True})

        after_delete = self.client.get(
            f"/api/sessions/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(after_delete.status_code, 404)


if __name__ == "__main__":
    unittest.main()
