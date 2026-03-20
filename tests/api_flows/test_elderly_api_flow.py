from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import api.server as server
import multi_agent_system_v2 as workflow
from tests.api_flows.support import APIFlowTestCase, build_fake_workflow_results


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
        expected_stem = f"report_{report_id}_82岁男"
        self.assertTrue((self.workspace_dir / session_id / f"{expected_stem}.json").exists())
        self.assertTrue((self.workspace_dir / session_id / f"{expected_stem}.md").exists())
        self.assertTrue(any(self.reports_dir.rglob(f"{expected_stem}.json")))
        self.assertTrue(any(self.reports_dir.rglob(f"{expected_stem}.md")))

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

    def test_chat_generated_report_uses_unified_storage_and_filename(self):
        start = self._start_chat()
        session_id = start["sessionId"]
        elderly_id = start["userId"]
        elderly_token = start["accessToken"]

        self.conversation_manager.store.update_profile(
            elderly_id,
            {
                "age": 83,
                "sex": "女",
                "residence": "城市",
                "education_years": 9,
                "hypertension": "是",
            },
        )
        self.conversation_manager._session_cache[session_id]["state"] = server.SessionState.CONFIRMING

        with patch.object(
            self.conversation_manager.orchestrator,
            "run",
            return_value=build_fake_workflow_results(),
        ):
            response = self._send_elderly_message(elderly_token, session_id, "确认")

        self.assertEqual(response["state"], "completed")
        self.assertTrue(response["completed"])

        session_detail = self.client.get(
            f"/api/sessions/{session_id}",
            headers=self._auth_headers(elderly_token),
        )
        self.assertEqual(session_detail.status_code, 200, session_detail.text)
        detail_body = session_detail.json()
        self.assertTrue(detail_body["reports"])
        report_id = detail_body["reports"][-1]["report_id"]
        expected_stem = f"report_{report_id}_83岁女"
        self.assertTrue((self.workspace_dir / session_id / f"{expected_stem}.json").exists())
        self.assertTrue((self.workspace_dir / session_id / f"{expected_stem}.md").exists())
        self.assertTrue(any(self.reports_dir.rglob(f"{expected_stem}.json")))
        self.assertTrue(any(self.reports_dir.rglob(f"{expected_stem}.md")))

    def test_report_stream_emits_real_stage_events(self):
        start = self._start_chat()
        session_id = start["sessionId"]
        elderly_token = start["accessToken"]

        def fake_run(profile, verbose=False, stage_callback=None):
            sequence = [
                ("status", "正在判定功能状态...", "功能状态判定完成"),
                ("risk", "正在进行风险预测...", "风险预测完成"),
                ("factors", "正在提取关键影响因素...", "关键影响因素分析完成"),
                ("actions", "正在生成干预行动建议...", "干预行动建议生成完成"),
                ("priority", "正在排序建议优先级...", "建议优先级排序完成"),
                ("review", "正在进行结果复核...", "结果复核完成"),
                ("report", "正在整理最终报告文本...", "最终报告文本整理完成"),
            ]
            for agent, running_message, completed_message in sequence:
                if stage_callback is not None:
                    stage_callback({"agent": agent, "status": "running", "message": running_message})
                    stage_callback({"agent": agent, "status": "completed", "message": completed_message})
            return build_fake_workflow_results()

        with patch.object(self.conversation_manager.orchestrator, "run", side_effect=fake_run):
            with self.client.stream(
                "POST",
                "/report/stream",
                json={
                    "sessionId": session_id,
                    "profile": {"age": 82, "sex": "男", "residence": "城市"},
                },
                headers=self._auth_headers(elderly_token),
            ) as response:
                self.assertEqual(response.status_code, 200)
                raw_body = "".join(response.iter_text())

        stage_events = []
        for line in raw_body.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            payload = json.loads(line[6:])
            if payload.get("type") == "agent_status":
                stage_events.append(payload["data"])

        self.assertIn(
            {"agent": "status", "status": "running", "message": "正在判定功能状态..."},
            stage_events,
        )
        self.assertIn(
            {"agent": "report", "status": "completed", "message": "最终报告文本整理完成"},
            stage_events,
        )

        ordered_running_agents = [
            event["agent"]
            for event in stage_events
            if event["status"] == "running" and event["agent"] != "orchestrator"
        ]
        self.assertEqual(
            ordered_running_agents,
            ["status", "risk", "factors", "actions", "priority", "review", "report"],
        )

    def test_llm_call_retries_after_connection_error(self):
        agent = workflow.BaseAgent("RetryAgent", "你是测试助手。")
        attempts = {"count": 0}

        def fake_create(**kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("Connection error.")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="重试成功")
                    )
                ]
            )

        with patch.object(workflow.client.chat.completions, "create", side_effect=fake_create):
            content = agent.call_llm("请返回测试结果")

        self.assertEqual(content, "重试成功")
        self.assertEqual(attempts["count"], 2)

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
