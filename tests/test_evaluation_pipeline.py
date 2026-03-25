from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR / "code"))

from evaluation.evaluator import ReportEvaluator  # noqa: E402
from evaluation.metrics import EvidenceCoverageMetric  # noqa: E402
from evaluation.utils import build_input_evidence_text, build_retrieved_context_text  # noqa: E402


class EvaluationPipelineTestCase(unittest.TestCase):
    def test_build_retrieved_context_prefers_hierarchical_payload(self):
        knowledge = {
            "evidence_cards": [
                {
                    "need": "夜间跌倒预防",
                    "recommendation": "安装感应夜灯",
                    "evidence_quote": "建议在卧室到卫生间路径安装感应夜灯。",
                    "doc_name": "跌倒预防指南.pdf",
                    "path": "环境改造 > 夜间照明",
                    "applicability": "独居老人夜间风险较高",
                }
            ],
            "selected_nodes": [
                {
                    "doc_name": "跌倒预防指南.pdf",
                    "path": "环境改造 > 夜间照明",
                    "summary": "安装感应夜灯并清理绊倒物。",
                    "text": "建议在卧室到卫生间路径安装感应夜灯，并移除地面绊倒物。",
                }
            ],
            "combined_context": "旧版 combined context",
        }

        short_context = build_retrieved_context_text(knowledge, use_full_text=False)
        full_context = build_retrieved_context_text(knowledge, use_full_text=True)

        self.assertIn("[证据1]", short_context)
        self.assertIn("安装感应夜灯", short_context)
        self.assertIn("移除地面绊倒物", full_context)

    def test_build_input_evidence_text_contains_upstream_sections(self):
        text = build_input_evidence_text(
            results={
                "status": {"status_name": "需要部分协助"},
                "risk": {"overall_risk_level": "高"},
                "factors": {"main_problems": ["独居"]},
                "actions": {"actions": [{"title": "安装夜灯"}]},
                "priority": {"priority_a": [{"title": "安装夜灯"}]},
            },
            profile={"age": 87, "sex": "女"},
        )
        self.assertIn("用户画像", text)
        self.assertIn("状态判定", text)
        self.assertIn("行动计划", text)

    @patch("evaluation.metrics.call_llm")
    def test_report_evaluator_outputs_new_metrics(self, mock_call_llm):
        mock_call_llm.side_effect = [
            json.dumps(
                [
                    {"statement": "该老人独居。", "source_type": "input", "reason": "来自画像"},
                    {"statement": "建议安装感应夜灯。", "source_type": "guideline", "reason": "来自知识证据"},
                ],
                ensure_ascii=False,
            ),
            json.dumps([{"index": 1, "supported": True, "reason": "画像有独居信息"}], ensure_ascii=False),
            json.dumps([{"index": 1, "supported": True, "reason": "证据卡支持"}], ensure_ascii=False),
            json.dumps(
                [
                    {"index": 1, "covered": True, "evidence": "87岁"},
                    {"index": 2, "covered": True, "evidence": "女"},
                    {"index": 3, "covered": True, "evidence": "独居"},
                ],
                ensure_ascii=False,
            ),
            json.dumps([{"index": 1, "relevant": True, "reason": "匹配跌倒预防"}], ensure_ascii=False),
            json.dumps([{"index": 1, "covered": True, "evidence": "安装感应夜灯"}], ensure_ascii=False),
        ]

        results = {
            "report": "该老人独居。建议安装感应夜灯。",
            "status": {"status_name": "需要部分协助"},
            "risk": {"short_term_risks": [{"risk": "夜间跌倒"}], "medium_term_risks": []},
            "factors": {"main_problems": ["独居"]},
            "actions": {"actions": [{"title": "安装夜灯"}]},
            "priority": {"priority_a": [{"title": "安装夜灯"}]},
            "knowledge": {
                "retrieval_mode": "hierarchical_llm",
                "retrieval_brief": {"focus_needs": ["夜间跌倒"], "text": "独居老人夜间跌倒预防"},
                "selected_docs": [
                    {"doc_id": "doc_fall", "doc_name": "跌倒预防指南.pdf", "doc_summary": "跌倒预防", "reason": "匹配夜间跌倒", "relevance_to_case": "夜间跌倒预防"}
                ],
                "selected_nodes": [
                    {"node_id": "node_fall", "doc_name": "跌倒预防指南.pdf", "path": "环境改造 > 夜间照明", "summary": "安装夜灯", "text": "建议安装感应夜灯。"}
                ],
                "evidence_cards": [
                    {"node_id": "node_fall", "need": "夜间跌倒", "recommendation": "安装感应夜灯", "evidence_quote": "建议安装感应夜灯。", "doc_name": "跌倒预防指南.pdf", "path": "环境改造 > 夜间照明", "applicability": "独居老人"}
                ],
            },
        }
        profile = {"age": 87, "sex": "女", "living_arrangement": "独居"}

        evaluator = ReportEvaluator()
        evaluation = evaluator.evaluate(results, profile)
        summary = evaluation.summary()

        self.assertEqual(summary["input_grounding"], 1.0)
        self.assertEqual(summary["guideline_grounding"], 1.0)
        self.assertEqual(summary["doc_routing_relevance"], 1.0)
        self.assertEqual(summary["node_evidence_relevance"], 1.0)
        self.assertEqual(summary["evidence_coverage"], 1.0)
        self.assertIn("selected_docs_count", evaluation.metadata)

    @patch("evaluation.metrics.call_llm")
    def test_evidence_coverage_tolerates_non_numeric_index(self, mock_call_llm):
        mock_call_llm.return_value = json.dumps(
            [
                {
                    "index": "高血压相关急性心脑血管事件风险",
                    "covered": True,
                    "evidence": "控制血压并识别危险信号",
                }
            ],
            ensure_ascii=False,
        )

        metric = EvidenceCoverageMetric()
        result = metric.evaluate(
            ["高血压相关急性心脑血管事件风险"],
            [{"need": "高血压相关急性心脑血管事件风险", "recommendation": "控制血压并识别危险信号"}],
        )

        self.assertEqual(result.score, 1.0)
        self.assertTrue(result.needs[0]["covered"])


if __name__ == "__main__":
    unittest.main()
