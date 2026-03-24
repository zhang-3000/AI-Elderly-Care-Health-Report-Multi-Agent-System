from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "code"))
sys.path.insert(0, str(BACKEND_DIR / "api"))

from report_utils import (  # noqa: E402
    LEGACY_REPORT_TITLE,
    REPORT_TITLE,
    generate_markdown_report,
)


class ReportUtilsTestCase(unittest.TestCase):
    def test_generate_markdown_report_keeps_only_modern_action_plan(self):
        duplicated_report = """# 养老健康评估报告

## 报告信息

- **生成时间**: 2026年03月25日 04:44

## 5. 完整评估报告

# 健康评估与照护行动计划

## 0. 报告说明
本报告基于当前信息生成，仅供参考。

## 1. 健康报告总结
1. 整体情况稳定。
2. 近期需要关注跌倒风险。
3. 请优先落实居家安全改造。

## 5. 温馨寄语
请和家人一起按计划慢慢落实。
"""
        markdown = generate_markdown_report(
            profile={"age": 75, "sex": "男"},
            results={"report": duplicated_report},
            report_data={"summary": "整体情况稳定；近期需要关注跌倒风险；请优先落实居家安全改造。"},
            timestamp=datetime(2026, 3, 25, 4, 44),
        )

        self.assertTrue(markdown.startswith(f"# {REPORT_TITLE}"))
        self.assertNotIn(LEGACY_REPORT_TITLE, markdown)
        self.assertNotIn("## 5. 完整评估报告", markdown)
        self.assertEqual(markdown.count(f"# {REPORT_TITLE}"), 1)

    def test_generate_markdown_report_falls_back_to_modern_template(self):
        markdown = generate_markdown_report(
            profile={"age": 82, "sex": "女"},
            results={
                "status": {"status_name": "需要部分协助", "status_description": "步态变慢，需要家属协助。"},
            },
            report_data={
                "summary": "整体情况需要持续观察；当前重点是跌倒风险；建议先整理居家环境。",
                "healthPortrait": {
                    "functionalStatus": "步态变慢，需要家属协助。",
                    "strengths": ["家属支持较好"],
                    "problems": ["步态不稳"],
                },
                "riskFactors": {
                    "shortTerm": [{"name": "跌倒", "description": "步态不稳，夜间如厕易跌倒", "timeframe": "1-4周"}],
                    "midTerm": [],
                },
                "recommendations": {
                    "priority1": [{"title": "整理居家环境", "description": "移除绊倒风险；本周内完成环境整理"}],
                    "priority2": [],
                    "priority3": [],
                },
            },
            timestamp=datetime(2026, 3, 25, 4, 44),
        )

        self.assertTrue(markdown.startswith(f"# {REPORT_TITLE}"))
        self.assertIn("## 0. 报告说明", markdown)
        self.assertIn("## 4. 健康建议", markdown)
        self.assertNotIn(LEGACY_REPORT_TITLE, markdown)


if __name__ == "__main__":
    unittest.main()
