"""
三个评测指标的实现：Faithfulness、Profile Coverage、Context Relevance。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from evaluation.utils import (
    call_llm,
    extract_profile_elements,
    split_chinese_sentences,
    parse_json_response,
    build_retrieved_context_text,
)

logger = logging.getLogger(__name__)


# ── 数据结构 ────────────────────────────────────────────────────────


@dataclass
class FaithfulnessResult:
    score: float
    total_statements: int
    supported_statements: int
    statements: List[Dict[str, Any]] = field(default_factory=list)
    """每条陈述的详情: {"statement": str, "supported": bool, "reason": str}"""


@dataclass
class ProfileCoverageResult:
    score: float
    total_elements: int
    covered_elements: int
    elements: List[Dict[str, Any]] = field(default_factory=list)
    """每个要素的详情: {"element": str, "covered": bool, "evidence": str}"""


@dataclass
class ContextRelevanceResult:
    score: float
    total_sentences: int
    useful_sentences: int
    useful_sentence_list: List[str] = field(default_factory=list)


# ── Faithfulness（忠实度）────────────────────────────────────────────


class FaithfulnessMetric:
    """
    评估行动计划中每一条事实陈述是否能从检索上下文中找到依据。
    得分 = 被支持的陈述数 / 总陈述数。
    """

    def evaluate(self, report_text: str, retrieved_context: str) -> FaithfulnessResult:
        if not retrieved_context.strip():
            logger.warning("Faithfulness: 检索上下文为空，跳过评测")
            return FaithfulnessResult(score=0.0, total_statements=0, supported_statements=0)

        # Step 1: 拆解报告为独立事实陈述
        statements = self._extract_statements(report_text)
        if not statements:
            logger.warning("Faithfulness: 未能从报告中提取到陈述")
            return FaithfulnessResult(score=0.0, total_statements=0, supported_statements=0)

        # Step 2: 批量验证陈述
        verification = self._verify_statements(statements, retrieved_context)

        supported = sum(1 for v in verification if v["supported"])
        score = supported / len(verification) if verification else 0.0

        return FaithfulnessResult(
            score=round(score, 4),
            total_statements=len(verification),
            supported_statements=supported,
            statements=verification,
        )

    def _extract_statements(self, report_text: str) -> List[str]:
        """使用 LLM 将报告拆解为独立的事实陈述。"""
        prompt = f"""请将以下健康评估与照护行动计划拆解成独立的事实陈述，每行一条。

要求：
- 只提取事实性陈述（如"该老人患有高血压"、"建议每天做腿部锻炼"）
- 不要提取修辞性、鼓励性、过渡性语句（如"咱们慢慢来"、"这是非常好的"）
- 每条陈述应该是完整的、可独立判断真假的句子
- 直接输出陈述列表，每行一条，不要编号

行动计划内容：
{report_text}"""

        response = call_llm(prompt, max_tokens=4096)
        lines = [line.strip() for line in response.split("\n") if line.strip()]
        # 过滤掉过短或明显非陈述的行
        statements = [
            re.sub(r"^[\d\.\-\*\s]+", "", line).strip()
            for line in lines
        ]
        statements = [s for s in statements if len(s) > 8]
        logger.info("Faithfulness: 提取到 %d 条陈述", len(statements))
        return statements

    def _verify_statements(
        self, statements: List[str], context: str
    ) -> List[Dict[str, Any]]:
        """批量验证陈述是否被上下文支持。"""
        numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(statements))

        prompt = f"""你是一个严谨的事实核查员。请判断以下每条陈述是否能从给定的参考上下文中找到依据或合理推导出来。

参考上下文：
{context}

需要验证的陈述：
{numbered}

请以 JSON 数组格式输出，每个元素包含：
- "index": 陈述编号（从1开始）
- "supported": true 或 false
- "reason": 简要说明判断理由（20字以内）

只输出 JSON 数组，不要其他内容。"""

        response = call_llm(prompt, max_tokens=4096)

        try:
            results = parse_json_response(response)
        except Exception:
            logger.warning("Faithfulness: 无法解析 LLM 验证结果，按行解析")
            results = self._fallback_parse(response, len(statements))

        # 对齐结果
        verification = []
        for i, stmt in enumerate(statements):
            matched = next((r for r in results if r.get("index") == i + 1), None)
            verification.append({
                "statement": stmt,
                "supported": matched.get("supported", False) if matched else False,
                "reason": matched.get("reason", "未获取到判断") if matched else "未获取到判断",
            })

        return verification

    def _fallback_parse(self, response: str, count: int) -> List[Dict[str, Any]]:
        """回退解析：按行判断 Yes/No。"""
        results = []
        lines = [l.strip() for l in response.split("\n") if l.strip()]
        for i, line in enumerate(lines[:count]):
            supported = "是" in line or "true" in line.lower() or "yes" in line.lower()
            results.append({"index": i + 1, "supported": supported, "reason": line[:30]})
        return results


# ── Profile Coverage（画像覆盖度）──────────────────────────────────


class ProfileCoverageMetric:
    """
    评估行动计划是否覆盖了用户画像中的关键要素。
    得分 = 被覆盖的要素数 / 总要素数。
    """

    def evaluate(
        self, report_text: str, profile: Dict[str, Any]
    ) -> ProfileCoverageResult:
        elements = extract_profile_elements(profile)
        if not elements:
            logger.warning("ProfileCoverage: 未从画像中提取到关键要素")
            return ProfileCoverageResult(score=0.0, total_elements=0, covered_elements=0)

        coverage = self._check_coverage(elements, report_text)
        covered = sum(1 for c in coverage if c["covered"])
        score = covered / len(coverage) if coverage else 0.0

        return ProfileCoverageResult(
            score=round(score, 4),
            total_elements=len(coverage),
            covered_elements=covered,
            elements=coverage,
        )

    def _check_coverage(
        self, elements: List[str], report_text: str
    ) -> List[Dict[str, Any]]:
        """使用 LLM 检查报告是否覆盖了每个要素。"""
        numbered = "\n".join(f"{i+1}. {e}" for i, e in enumerate(elements))

        prompt = f"""你是一个评测助手。请判断以下健康评估与照护行动计划是否提到或覆盖了每个关键要素。
"覆盖"的含义是：报告中直接提及了该要素，或者在相关内容中间接涉及了该信息。

行动计划内容：
{report_text}

需要检查的关键要素：
{numbered}

请以 JSON 数组格式输出，每个元素包含：
- "index": 要素编号（从1开始）
- "covered": true 或 false
- "evidence": 报告中的相关内容片段（如果覆盖了），或 "未提及"

只输出 JSON 数组，不要其他内容。"""

        response = call_llm(prompt, max_tokens=4096)

        try:
            results = parse_json_response(response)
        except Exception:
            logger.warning("ProfileCoverage: 无法解析 LLM 结果")
            results = []

        coverage = []
        for i, elem in enumerate(elements):
            matched = next((r for r in results if r.get("index") == i + 1), None)
            coverage.append({
                "element": elem,
                "covered": matched.get("covered", False) if matched else False,
                "evidence": matched.get("evidence", "未获取到判断") if matched else "未获取到判断",
            })

        return coverage


# ── Context Relevance（上下文相关性）────────────────────────────────


class ContextRelevanceMetric:
    """
    评估检索上下文的精准度：有用句子占比。
    得分 = 有用句子数 / 总句子数。
    """

    def evaluate(
        self, query_description: str, retrieved_context: str
    ) -> ContextRelevanceResult:
        if not retrieved_context.strip():
            logger.warning("ContextRelevance: 检索上下文为空，跳过评测")
            return ContextRelevanceResult(score=0.0, total_sentences=0, useful_sentences=0)

        sentences = split_chinese_sentences(retrieved_context)
        if not sentences:
            return ContextRelevanceResult(score=0.0, total_sentences=0, useful_sentences=0)

        useful = self._extract_useful_sentences(query_description, retrieved_context)
        score = len(useful) / len(sentences) if sentences else 0.0

        return ContextRelevanceResult(
            score=round(min(score, 1.0), 4),
            total_sentences=len(sentences),
            useful_sentences=len(useful),
            useful_sentence_list=useful,
        )

    def _extract_useful_sentences(
        self, query: str, context: str
    ) -> List[str]:
        """使用 LLM 从上下文中提取有用句子。"""
        prompt = f"""给定以下关于一位老人的健康查询和检索到的参考上下文，请从上下文中摘出所有对回答该查询真正有用的句子。

要求：
- 只输出上下文中原文的句子，不要改写
- 每行一条
- 如果没有有用的内容，只回复"信息不足"

查询描述：{query}

参考上下文：
{context}"""

        response = call_llm(prompt, max_tokens=4096)

        if "信息不足" in response:
            return []

        lines = [line.strip() for line in response.split("\n") if line.strip()]
        # 过滤掉过短的行和非句子内容
        useful = [
            re.sub(r"^[\d\.\-\*\s]+", "", line).strip()
            for line in lines
        ]
        useful = [s for s in useful if len(s) > 8]
        return useful
