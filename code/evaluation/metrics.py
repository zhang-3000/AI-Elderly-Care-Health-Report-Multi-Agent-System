"""
分层检索与报告评测指标：
- Input Grounding
- Guideline Grounding
- Profile Coverage
- Doc Routing Relevance
- Node Evidence Relevance
- Evidence Coverage
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from evaluation.utils import call_llm, parse_json_response

logger = logging.getLogger(__name__)


@dataclass
class GroundingResult:
    score: float
    total_statements: int
    supported_statements: int
    statements: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ProfileCoverageResult:
    score: float
    total_elements: int
    covered_elements: int
    elements: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DocRoutingRelevanceResult:
    score: float
    total_docs: int
    relevant_docs: int
    docs: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class NodeEvidenceRelevanceResult:
    score: float
    total_nodes: int
    covered_nodes: int
    nodes: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class EvidenceCoverageResult:
    score: float
    total_needs: int
    covered_needs: int
    needs: List[Dict[str, Any]] = field(default_factory=list)


class ReportGroundingMetric:
    """按来源拆分报告陈述，并分别进行输入事实/指南证据核验。"""

    def evaluate(
        self,
        report_text: str,
        input_context: str,
        guideline_context: str,
    ) -> Tuple[GroundingResult, GroundingResult]:
        extracted = self._extract_statements(report_text)
        input_statements = [item for item in extracted if item["source_type"] == "input"]
        guideline_statements = [item for item in extracted if item["source_type"] == "guideline"]

        input_result = self._evaluate_group(
            input_statements,
            input_context,
            missing_reason="输入证据为空或未覆盖该陈述",
        )
        guideline_result = self._evaluate_group(
            guideline_statements,
            guideline_context,
            missing_reason="知识证据为空或未覆盖该陈述",
        )
        return input_result, guideline_result

    def _extract_statements(self, report_text: str) -> List[Dict[str, str]]:
        prompt = f"""请将以下健康评估与照护行动计划拆解成可以核验的陈述，并判断每条陈述主要应该由哪类证据支持。

证据来源定义：
- input：用户画像、状态判定、风险评估、因素分析、行动计划和优先级排序这些系统内部结果应能支持的内容
- guideline：知识检索得到的指南、共识、照护方法、训练建议等外部专业依据应能支持的内容
- other：免责说明、温馨寄语、鼓励性语言，不纳入核验

要求：
- 每条陈述必须是完整句子
- 只输出 JSON 数组
- 每个元素包含 statement/source_type/reason

行动计划内容：
{report_text}"""
        response = call_llm(prompt, max_tokens=4096)
        try:
            parsed = parse_json_response(response)
            if isinstance(parsed, list):
                items = parsed
            else:
                items = parsed.get("statements", [])
            output = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                statement = str(item.get("statement") or "").strip()
                source_type = str(item.get("source_type") or "").strip().lower()
                if len(statement) <= 4:
                    continue
                if source_type not in {"input", "guideline", "other"}:
                    source_type = "other"
                output.append(
                    {
                        "statement": statement,
                        "source_type": source_type,
                        "reason": str(item.get("reason") or "").strip(),
                    }
                )
            if output:
                return output
        except Exception:
            logger.warning("Grounding: 无法解析陈述分类结果，使用启发式回退")
        return self._fallback_extract_statements(report_text)

    def _fallback_extract_statements(self, report_text: str) -> List[Dict[str, str]]:
        sentences = [
            line.strip()
            for line in re.split(r"[。\n]", report_text)
            if len(line.strip()) > 4
        ]
        output = []
        guideline_markers = ("建议", "应", "推荐", "安装", "复查", "训练", "锻炼", "夜灯", "扶手")
        other_markers = ("本报告", "不能替代", "温馨寄语", "别着急", "慢慢来")
        for sentence in sentences:
            source_type = "input"
            if any(marker in sentence for marker in other_markers):
                source_type = "other"
            elif any(marker in sentence for marker in guideline_markers):
                source_type = "guideline"
            output.append({"statement": sentence, "source_type": source_type, "reason": "fallback"})
        return output

    def _evaluate_group(
        self,
        statements: Sequence[Dict[str, str]],
        context: str,
        missing_reason: str,
    ) -> GroundingResult:
        if not statements:
            return GroundingResult(score=0.0, total_statements=0, supported_statements=0, statements=[])
        if not context.strip():
            details = [
                {
                    "statement": item["statement"],
                    "supported": False,
                    "reason": missing_reason,
                    "source_type": item["source_type"],
                }
                for item in statements
            ]
            return GroundingResult(score=0.0, total_statements=len(details), supported_statements=0, statements=details)

        numbered = "\n".join(f"{idx + 1}. {item['statement']}" for idx, item in enumerate(statements))
        prompt = f"""你是一个严谨的事实核查员。请判断以下每条陈述是否能从参考上下文中直接支持，或由上下文合理推出。

参考上下文：
{context}

待核验陈述：
{numbered}

只输出 JSON 数组。每个元素包含：
- index: 陈述编号（从1开始）
- supported: true/false
- reason: 20字以内说明"""
        response = call_llm(prompt, max_tokens=4096)
        try:
            parsed = parse_json_response(response)
            if isinstance(parsed, dict):
                parsed = parsed.get("items", [])
        except Exception:
            logger.warning("Grounding: 无法解析核验结果，使用行级回退")
            parsed = []

        details = []
        supported_count = 0
        for idx, item in enumerate(statements, start=1):
            matched = next(
                (
                    row for row in parsed
                    if isinstance(row, dict) and int(row.get("index", -1)) == idx
                ),
                None,
            )
            supported = bool(matched.get("supported")) if matched else False
            if supported:
                supported_count += 1
            details.append(
                {
                    "statement": item["statement"],
                    "supported": supported,
                    "reason": str(matched.get("reason") or "未获取到判断") if matched else "未获取到判断",
                    "source_type": item["source_type"],
                }
            )
        score = supported_count / len(details) if details else 0.0
        return GroundingResult(
            score=round(score, 4),
            total_statements=len(details),
            supported_statements=supported_count,
            statements=details,
        )


class ProfileCoverageMetric:
    """评估报告是否覆盖用户画像关键要素。"""

    def evaluate(self, report_text: str, elements: List[str]) -> ProfileCoverageResult:
        if not elements:
            return ProfileCoverageResult(score=0.0, total_elements=0, covered_elements=0)

        numbered = "\n".join(f"{idx + 1}. {element}" for idx, element in enumerate(elements))
        prompt = f"""请判断以下健康评估与照护行动计划是否提到或覆盖了每个关键要素。

行动计划内容：
{report_text}

关键要素：
{numbered}

请只输出 JSON 数组，每个元素包含：
- index: 要素编号
- covered: true/false
- evidence: 报告中的相关内容片段，或“未提及”"""
        response = call_llm(prompt, max_tokens=4096)
        try:
            parsed = parse_json_response(response)
            if isinstance(parsed, dict):
                parsed = parsed.get("items", [])
        except Exception:
            logger.warning("ProfileCoverage: 无法解析 LLM 结果")
            parsed = []

        details = []
        covered_count = 0
        for idx, element in enumerate(elements, start=1):
            matched = next(
                (
                    row for row in parsed
                    if isinstance(row, dict) and int(row.get("index", -1)) == idx
                ),
                None,
            )
            covered = bool(matched.get("covered")) if matched else False
            if covered:
                covered_count += 1
            details.append(
                {
                    "element": element,
                    "covered": covered,
                    "evidence": str(matched.get("evidence") or "未获取到判断") if matched else "未获取到判断",
                }
            )
        score = covered_count / len(details) if details else 0.0
        return ProfileCoverageResult(
            score=round(score, 4),
            total_elements=len(details),
            covered_elements=covered_count,
            elements=details,
        )


class DocRoutingRelevanceMetric:
    """评估第一层文档路由是否选到了对病例有用的文档。"""

    def evaluate(self, retrieval_brief: Dict[str, Any], selected_docs: List[Dict[str, Any]]) -> DocRoutingRelevanceResult:
        if not selected_docs:
            return DocRoutingRelevanceResult(score=0.0, total_docs=0, relevant_docs=0, docs=[])

        docs_text = "\n".join(
            [
                "\n".join(
                    [
                        f"{idx + 1}. {doc.get('doc_name')}",
                        f"   summary: {doc.get('doc_summary') or '无'}",
                        f"   selection_reason: {doc.get('reason') or '无'}",
                        f"   relevance_to_case: {doc.get('relevance_to_case') or '无'}",
                    ]
                )
                for idx, doc in enumerate(selected_docs)
            ]
        )
        prompt = f"""你是检索评测助手。请判断以下被选中的文档是否真的与病例检索需求相关。

病例摘要：
{retrieval_brief.get('text', '')}

被选文档：
{docs_text}

请只输出 JSON 数组。每个元素包含：
- index: 文档编号
- relevant: true/false
- reason: 20字以内理由"""
        response = call_llm(prompt, max_tokens=3072)
        try:
            parsed = parse_json_response(response)
            if isinstance(parsed, dict):
                parsed = parsed.get("items", [])
        except Exception:
            logger.warning("DocRoutingRelevance: 无法解析 LLM 结果")
            parsed = []

        details = []
        relevant_count = 0
        for idx, doc in enumerate(selected_docs, start=1):
            matched = next(
                (
                    row for row in parsed
                    if isinstance(row, dict) and int(row.get("index", -1)) == idx
                ),
                None,
            )
            relevant = bool(matched.get("relevant")) if matched else False
            if relevant:
                relevant_count += 1
            details.append(
                {
                    "doc_name": doc.get("doc_name"),
                    "relevant": relevant,
                    "reason": str(matched.get("reason") or "未获取到判断") if matched else "未获取到判断",
                }
            )
        score = relevant_count / len(details) if details else 0.0
        return DocRoutingRelevanceResult(
            score=round(score, 4),
            total_docs=len(details),
            relevant_docs=relevant_count,
            docs=details,
        )


class NodeEvidenceRelevanceMetric:
    """评估第二层节点路由是否真正产出了结构化证据。"""

    def evaluate(self, selected_nodes: List[Dict[str, Any]], evidence_cards: List[Dict[str, Any]]) -> NodeEvidenceRelevanceResult:
        if not selected_nodes:
            return NodeEvidenceRelevanceResult(score=0.0, total_nodes=0, covered_nodes=0, nodes=[])

        supported_node_ids = {
            str(card.get("node_id") or "").strip()
            for card in evidence_cards
            if str(card.get("node_id") or "").strip()
        }
        details = []
        covered_count = 0
        for node in selected_nodes:
            node_id = str(node.get("node_id") or "")
            covered = node_id in supported_node_ids
            if covered:
                covered_count += 1
            details.append(
                {
                    "node_id": node_id,
                    "doc_name": node.get("doc_name"),
                    "path": node.get("path"),
                    "covered": covered,
                    "reason": "有证据卡引用" if covered else "未产出证据卡",
                }
            )
        score = covered_count / len(details) if details else 0.0
        return NodeEvidenceRelevanceResult(
            score=round(score, 4),
            total_nodes=len(details),
            covered_nodes=covered_count,
            nodes=details,
        )


class EvidenceCoverageMetric:
    """评估高优先级需求是否被结构化证据卡覆盖。"""

    def evaluate(self, focus_needs: List[str], evidence_cards: List[Dict[str, Any]]) -> EvidenceCoverageResult:
        if not focus_needs:
            return EvidenceCoverageResult(score=0.0, total_needs=0, covered_needs=0, needs=[])
        if not evidence_cards:
            details = [{"need": need, "covered": False, "evidence": "无结构化证据卡"} for need in focus_needs]
            return EvidenceCoverageResult(score=0.0, total_needs=len(details), covered_needs=0, needs=details)

        prompt = f"""你是检索评测助手。请判断每个病例需求是否已被下面的结构化证据卡覆盖。

病例需求：
{json.dumps(focus_needs, ensure_ascii=False, indent=2)}

结构化证据卡：
{json.dumps(evidence_cards, ensure_ascii=False, indent=2)}

请只输出 JSON 数组。每个元素包含：
- index: 需求编号
- covered: true/false
- evidence: 对应证据卡里的 recommendation 或 need 摘要；若未覆盖则写“未覆盖”"""
        response = call_llm(prompt, max_tokens=3072)
        try:
            parsed = parse_json_response(response)
            if isinstance(parsed, dict):
                parsed = parsed.get("items", [])
        except Exception:
            logger.warning("EvidenceCoverage: 无法解析 LLM 结果，使用字符串回退")
            parsed = []

        details = []
        covered_count = 0
        for idx, need in enumerate(focus_needs, start=1):
            matched = next(
                (
                    row for row in parsed
                    if isinstance(row, dict) and self._matches_need_row(row, idx, need)
                ),
                None,
            )
            covered = bool(matched.get("covered")) if matched else self._fallback_match_need(need, evidence_cards)
            if covered:
                covered_count += 1
            details.append(
                {
                    "need": need,
                    "covered": covered,
                    "evidence": str(matched.get("evidence") or ("字符串匹配命中" if covered else "未覆盖")) if matched else ("字符串匹配命中" if covered else "未覆盖"),
                }
            )
        score = covered_count / len(details) if details else 0.0
        return EvidenceCoverageResult(
            score=round(score, 4),
            total_needs=len(details),
            covered_needs=covered_count,
            needs=details,
        )

    @staticmethod
    def _matches_need_row(row: Dict[str, Any], idx: int, need: str) -> bool:
        index_value = row.get("index")
        try:
            return int(index_value) == idx
        except (TypeError, ValueError):
            pass

        normalized_need = str(need or "").strip()
        for candidate in (row.get("need"), row.get("requirement"), row.get("index")):
            if str(candidate or "").strip() == normalized_need:
                return True
        return False

    @staticmethod
    def _fallback_match_need(need: str, evidence_cards: List[Dict[str, Any]]) -> bool:
        lowered = str(need or "").lower()
        for card in evidence_cards:
            haystack = " ".join(
                [
                    str(card.get("need") or ""),
                    str(card.get("recommendation") or ""),
                    str(card.get("applicability") or ""),
                ]
            ).lower()
            if lowered and lowered in haystack:
                return True
        return False
