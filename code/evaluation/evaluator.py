"""
评测编排器 - 协调三个指标完成报告评测。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from evaluation.metrics import (
    ContextRelevanceMetric,
    ContextRelevanceResult,
    FaithfulnessMetric,
    FaithfulnessResult,
    ProfileCoverageMetric,
    ProfileCoverageResult,
)
from evaluation.utils import build_retrieved_context_text, extract_profile_elements

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """三个指标的评测结果汇总。"""

    faithfulness: Optional[FaithfulnessResult] = None
    profile_coverage: Optional[ProfileCoverageResult] = None
    context_relevance: Optional[ContextRelevanceResult] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        """返回简洁的分数汇总。"""
        result: Dict[str, Any] = {}
        if self.faithfulness:
            result["faithfulness"] = self.faithfulness.score
        if self.profile_coverage:
            result["profile_coverage"] = self.profile_coverage.score
        if self.context_relevance:
            result["context_relevance"] = self.context_relevance.score
        return result

    def to_dict(self) -> Dict[str, Any]:
        """返回完整的评测结果字典。"""
        return {
            "summary": self.summary(),
            "faithfulness": asdict(self.faithfulness) if self.faithfulness else None,
            "profile_coverage": asdict(self.profile_coverage) if self.profile_coverage else None,
            "context_relevance": asdict(self.context_relevance) if self.context_relevance else None,
            "metadata": self.metadata,
        }


class ReportEvaluator:
    """
    报告评测编排器。

    支持两种模式：
    1. 在线评测：传入 results dict 和 profile
    2. 离线评测：从 JSON 文件加载，可选重新运行 RAG 检索
    """

    def __init__(self, rag_agent=None, knowledge_agent=None):
        """
        Args:
            rag_agent: PageIndexRAGAgent 实例（用于离线评测时重新检索）
            knowledge_agent: KnowledgeAgent 实例（用于离线评测时重新检索）
        """
        self.rag_agent = rag_agent
        self.knowledge_agent = knowledge_agent
        self.faithfulness_metric = FaithfulnessMetric()
        self.coverage_metric = ProfileCoverageMetric()
        self.context_relevance_metric = ContextRelevanceMetric()

    def evaluate(
        self,
        results: Dict[str, Any],
        profile: Dict[str, Any],
        run_faithfulness: bool = True,
        run_coverage: bool = True,
        run_context_relevance: bool = True,
    ) -> EvaluationResult:
        """
        评测一份报告。

        Args:
            results: orchestrator.run() 返回的完整结果字典
            profile: 用户画像字典
            run_faithfulness: 是否运行忠实度评测
            run_coverage: 是否运行覆盖度评测
            run_context_relevance: 是否运行上下文相关性评测

        Returns:
            EvaluationResult 包含各指标得分和详情
        """
        report_text = results.get("report", "")
        knowledge = results.get("knowledge", {})

        # Faithfulness 用完整 text 做深度验证
        full_context = build_retrieved_context_text(knowledge, use_full_text=True)
        # Context Relevance 用 excerpt/combined_context（与 LLM 实际看到的一致）
        short_context = build_retrieved_context_text(knowledge, use_full_text=False)

        eval_result = EvaluationResult()

        # Faithfulness - 需要检索上下文
        if run_faithfulness:
            if full_context:
                logger.info("开始评测 Faithfulness...")
                eval_result.faithfulness = self.faithfulness_metric.evaluate(
                    report_text, full_context
                )
                logger.info(
                    "Faithfulness 得分: %.4f (%d/%d)",
                    eval_result.faithfulness.score,
                    eval_result.faithfulness.supported_statements,
                    eval_result.faithfulness.total_statements,
                )
            else:
                logger.warning("Faithfulness: 无检索上下文，跳过")

        # Profile Coverage - 不需要检索上下文
        if run_coverage:
            logger.info("开始评测 Profile Coverage...")
            eval_result.profile_coverage = self.coverage_metric.evaluate(
                report_text, profile
            )
            logger.info(
                "Profile Coverage 得分: %.4f (%d/%d)",
                eval_result.profile_coverage.score,
                eval_result.profile_coverage.covered_elements,
                eval_result.profile_coverage.total_elements,
            )

        # Context Relevance - 需要检索上下文
        if run_context_relevance:
            if short_context:
                age = profile.get("age", "")
                sex = profile.get("sex", "")
                query_desc = f"为{age}岁{sex}性老人生成健康评估和照护行动计划"
                logger.info("开始评测 Context Relevance...")
                eval_result.context_relevance = self.context_relevance_metric.evaluate(
                    query_desc, short_context
                )
                logger.info(
                    "Context Relevance 得分: %.4f (%d/%d)",
                    eval_result.context_relevance.score,
                    eval_result.context_relevance.useful_sentences,
                    eval_result.context_relevance.total_sentences,
                )
            else:
                logger.warning("Context Relevance: 无检索上下文，跳过")

        eval_result.metadata = {
            "has_rag_context": bool(full_context),
            "report_length": len(report_text),
            "full_context_length": len(full_context),
            "short_context_length": len(short_context),
            "profile_elements_count": len(extract_profile_elements(profile)),
        }

        return eval_result

    def evaluate_from_file(
        self,
        json_path: str,
        re_retrieve: bool = False,
    ) -> EvaluationResult:
        """
        从保存的 JSON 报告文件加载并评测。

        Args:
            json_path: 报告 JSON 文件路径
            re_retrieve: 是否重新运行 RAG 检索（当原始报告无 RAG 数据时需要）

        Returns:
            EvaluationResult
        """
        path = Path(json_path)
        if not path.exists():
            raise FileNotFoundError(f"报告文件不存在: {json_path}")

        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        profile = payload.get("profile", {})
        results = payload.get("raw_results", {})

        # 如果 raw_results 为空，尝试直接从 payload 获取
        if not results:
            results = {k: v for k, v in payload.items() if k not in ("profile", "report_id", "session_id", "user_id", "generated_at", "report_data")}

        # 如果需要重新检索且有 knowledge_agent
        if re_retrieve and self.knowledge_agent is not None:
            logger.info("重新运行 RAG 检索...")
            results["knowledge"] = self._re_retrieve(profile, results)

        return self.evaluate(results, profile)

    def _re_retrieve(
        self, profile: Dict[str, Any], results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """使用 KnowledgeAgent 重新检索。"""
        if self.knowledge_agent is None:
            return {"enabled": False, "combined_context": "", "total_hits": 0}

        try:
            from multi_agent_system_v2 import UserProfile

            # 从字典重建 UserProfile
            profile_copy = {k: v for k, v in profile.items() if k != "user_type"}
            user_profile = UserProfile(**profile_copy)

            status_result = results.get("status", {})
            risk_result = results.get("risk", {})
            factor_result = results.get("factors", {})

            knowledge = self.knowledge_agent.retrieve_comprehensive(
                user_profile, status_result, risk_result, factor_result
            )
            logger.info("重新检索完成，总命中: %d", knowledge.get("total_hits", 0))
            return knowledge
        except Exception as e:
            logger.error("重新检索失败: %s", e)
            return {"enabled": False, "combined_context": "", "total_hits": 0}
