"""
报告生成与存储辅助函数。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from multi_agent_system_v2 import UserProfile

REPORT_TITLE = "健康评估与照护行动计划"
ALTERNATE_REPORT_TITLE = "健康评估与照顾行动计划"
LEGACY_REPORT_TITLE = "养老健康评估报告"
REPORT_DISCLAIMER = (
    "本报告基于您/家属提供的信息进行风险提示与照护建议，不能替代医生面诊；"
    "涉及用药与检查，请以医生意见为准。"
)
REPORT_FOOTER = "*本报告由 AI 养老健康助手自动生成，仅供参考。请结合专业医生的诊断和建议。*"


def profile_to_dict(profile: "UserProfile") -> Dict[str, Any]:
    """将 UserProfile 转为可持久化字典。"""
    payload = asdict(profile)
    payload.pop("user_type", None)
    return payload


def build_report_file_stem(report_id: str, profile: Dict[str, Any]) -> str:
    """构造统一的报告文件名 stem。"""
    age = profile.get("age", "未知")
    sex = profile.get("sex", "未知")
    return f"report_{report_id}_{age}岁{sex}"


def save_report_bundle(
    reports_dir: Path,
    workspace_manager,
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    保存报告到传统目录与工作区，返回完整 JSON 载荷。
    """
    timestamp = datetime.now()
    report_id = timestamp.strftime("%Y%m%d_%H%M%S_%f")

    date_dir = reports_dir / timestamp.strftime("%Y%m")
    date_dir.mkdir(parents=True, exist_ok=True)

    file_stem = build_report_file_stem(report_id, profile)
    payload = {
        "report_id": report_id,
        "session_id": session_id,
        "user_id": user_id,
        "generated_at": timestamp.isoformat(),
        "profile": profile,
        "raw_results": results,
        "report_data": report_data,
    }

    json_file = date_dir / f"{file_stem}.json"
    with open(json_file, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    markdown_file = date_dir / f"{file_stem}.md"
    markdown_content = generate_markdown_report(profile, results, report_data, timestamp)
    with open(markdown_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(markdown_content)

    if session_id and workspace_manager is not None:
        workspace_manager.save_report(session_id, payload, "json", f"{file_stem}.json")
        workspace_manager.save_report(session_id, markdown_content, "md", f"{file_stem}.md")
        workspace_manager.update_metadata(session_id, {"has_report": True})

    return payload


def generate_markdown_report(
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    timestamp: datetime,
) -> str:
    """生成 Markdown 格式的健康评估与照护行动计划。"""
    raw_report = _extract_modern_report(results.get("report", ""))
    if raw_report:
        return _ensure_report_footer(raw_report)

    return _render_report_from_structured_data(profile, results, report_data, timestamp)


def _normalize_markdown_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^```(?:markdown)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_modern_report(raw_report: Any) -> str:
    cleaned = _normalize_markdown_text(raw_report)
    if not cleaned:
        return ""

    title_variants = [f"# {REPORT_TITLE}", f"# {ALTERNATE_REPORT_TITLE}"]
    positions = [cleaned.find(title) for title in title_variants if cleaned.find(title) >= 0]
    if positions:
        cleaned = cleaned[min(positions) :].strip()
    elif re.match(r"^##\s*(?:0\.\s*报告说明|1\.\s*健康报告总结)", cleaned):
        cleaned = f"# {REPORT_TITLE}\n\n{cleaned}"
    else:
        return ""

    if cleaned.startswith(f"# {ALTERNATE_REPORT_TITLE}"):
        cleaned = cleaned.replace(f"# {ALTERNATE_REPORT_TITLE}", f"# {REPORT_TITLE}", 1)

    if cleaned.startswith(f"# {LEGACY_REPORT_TITLE}"):
        return ""

    return cleaned


def _ensure_report_footer(markdown: str) -> str:
    cleaned = _normalize_markdown_text(markdown)
    if not cleaned:
        return ""
    if REPORT_FOOTER in cleaned:
        return cleaned
    return f"{cleaned}\n\n---\n\n{REPORT_FOOTER}"


def _coerce_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _split_summary_lines(summary: Any) -> List[str]:
    text = str(summary or "").strip()
    if not text:
        return []

    numbered_lines = [
        re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    numbered_lines = [line for line in numbered_lines if line]
    if len(numbered_lines) > 1:
        return numbered_lines[:3]

    parts = [part.strip() for part in re.split(r"[；;]\s*", text) if part.strip()]
    return parts[:3] if parts else [text]


def _split_recommendation_description(description: Any) -> tuple[str, str]:
    text = str(description or "").strip()
    if not text:
        return "请结合医生和家属安排稳步落实。", "按计划执行并记录完成情况。"

    parts = [part.strip() for part in re.split(r"[；;]\s*", text) if part.strip()]
    if len(parts) == 1:
        return parts[0], "按计划执行并记录完成情况。"
    return parts[0], "；".join(parts[1:])


def _render_report_from_structured_data(
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    timestamp: datetime,
) -> str:
    status = results.get("status") if isinstance(results.get("status"), dict) else {}
    health_portrait = report_data.get("healthPortrait") if isinstance(report_data.get("healthPortrait"), dict) else {}
    risk_factors = report_data.get("riskFactors") if isinstance(report_data.get("riskFactors"), dict) else {}
    recommendations = report_data.get("recommendations") if isinstance(report_data.get("recommendations"), dict) else {}

    status_name = str(status.get("status_name") or status.get("status_description") or "待进一步评估").strip()
    status_description = str(
        health_portrait.get("functionalStatus") or status.get("status_description") or "当前功能状态信息待补充。"
    ).strip()
    summary_lines = _split_summary_lines(report_data.get("summary"))
    strengths = _coerce_string_list(health_portrait.get("strengths"))
    problems = _coerce_string_list(health_portrait.get("problems"))
    short_term = risk_factors.get("shortTerm") if isinstance(risk_factors.get("shortTerm"), list) else []
    mid_term = risk_factors.get("midTerm") if isinstance(risk_factors.get("midTerm"), list) else []

    md_lines = [
        f"# {REPORT_TITLE}",
        "",
        "## 0. 报告说明",
        REPORT_DISCLAIMER,
        "",
        "## 1. 健康报告总结",
    ]

    if summary_lines:
        md_lines.extend([f"{idx}. {item}" for idx, item in enumerate(summary_lines, start=1)])
    else:
        md_lines.append(
            f"1. 本次行动计划生成于 {timestamp.strftime('%Y年%m月%d日 %H:%M')}，已汇总当前可用的健康信息。"
        )
        md_lines.append("2. 当前需要优先关注安全风险与功能维护。")
        md_lines.append(
            f"3. 请结合{profile.get('age', '当前')}岁{profile.get('sex', '老人')}的实际情况，和家人一起逐项落实行动建议。"
        )

    md_lines.extend(
        [
            "",
            "## 2. 您的健康画像（现在“好在哪里、短板在哪里”）",
            f"### （1）功能状态：{status_name}",
            status_description,
            "",
            "### （2）优势（需要继续保持）",
        ]
    )

    if strengths:
        md_lines.extend([f"* {item}" for item in strengths])
    else:
        md_lines.append("* 当前优势信息待进一步补充。")

    md_lines.extend(["", "### （3）主要问题（本计划重点要解决的）"])
    if problems:
        md_lines.extend([f"{idx}. **{item}**" for idx, item in enumerate(problems, start=1)])
    else:
        md_lines.append("1. **当前主要问题待进一步补充**")

    md_lines.extend(["", "## 3. 风险因素（按时间或优先级最高的事来写，方便落地）", "### 近期（1-4周）重点风险："])
    if short_term:
        for item in short_term:
            name = str(item.get("name") or "近期风险").strip()
            description = str(item.get("description") or "暂无描述").strip()
            timeframe = str(item.get("timeframe") or "").strip()
            suffix = f"（时间范围：{timeframe}）" if timeframe else ""
            md_lines.append(f"* **{name}**：{description}{suffix}")
    else:
        md_lines.append("* 暂未识别出明确的近期高优先级风险。")

    md_lines.extend(["", "### 中期（1-6月）重点风险："])
    if mid_term:
        for item in mid_term:
            name = str(item.get("name") or "中期风险").strip()
            description = str(item.get("description") or "暂无描述").strip()
            timeframe = str(item.get("timeframe") or "").strip()
            suffix = f"（时间范围：{timeframe}）" if timeframe else ""
            md_lines.append(f"* **{name}**：{description}{suffix}")
    else:
        md_lines.append("* 暂未识别出明确的中期高优先级风险。")

    md_lines.extend(
        [
            "",
            "**注：** 风险提示不代表一定会发生，而是提醒优先把“最要命、最可防”的环节补上。",
            "",
            "## 4. 健康建议",
        ]
    )

    for section_title, items in [
        ("A. 第一优先级", recommendations.get("priority1")),
        ("B. 第二优先级", recommendations.get("priority2")),
        ("C. 第三优先级", recommendations.get("priority3")),
    ]:
        md_lines.extend(["", f"### {section_title}"])
        if isinstance(items, list) and items:
            for index, item in enumerate(items, start=1):
                how_to_do, completion = _split_recommendation_description(
                    item.get("description") if isinstance(item, dict) else ""
                )
                title = str(item.get("title") or "待执行事项").strip() if isinstance(item, dict) else "待执行事项"
                md_lines.extend(
                    [
                        f"**{index}）{title}**",
                        f"* **怎么做**：{how_to_do}",
                        f"* **完成标准**：{completion}",
                        "",
                    ]
                )
            if md_lines[-1] == "":
                md_lines.pop()
        else:
            md_lines.append("* 当前暂无该优先级的行动建议。")

    md_lines.extend(
        [
            "",
            "## 5. 温馨寄语",
            "请和家人按计划一步一步来，优先把安全、活动和日常照护落实好；过程中如果出现明显不适或情况变化，请及时联系医生。",
            "",
            "---",
            "",
            REPORT_FOOTER,
        ]
    )
    return "\n".join(md_lines)


def build_report_list_item(payload: Dict[str, Any], fallback_id: str) -> Dict[str, Any]:
    """构造列表场景使用的报告摘要对象。"""
    report_data = payload.get("report_data") if isinstance(payload, dict) else {}
    title = REPORT_TITLE
    if isinstance(report_data, dict):
        title = report_data.get("summary") or title

    return {
        "id": payload.get("report_id", fallback_id),
        "title": title,
        "created_at": payload.get("generated_at") or "",
        "content": payload,
    }


def list_reports_for_user(workspace_manager, user_id: str) -> List[Dict[str, Any]]:
    """按用户归属聚合工作区中的报告。"""
    reports: List[Dict[str, Any]] = []
    for metadata in workspace_manager.find_sessions_by_user(user_id):
        session_id = metadata.get("session_id")
        if not session_id:
            continue

        for report_file in workspace_manager.get_report_files(session_id):
            with open(report_file, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)

            item = build_report_list_item(payload, report_file.stem)
            if not item["created_at"]:
                item["created_at"] = datetime.fromtimestamp(report_file.stat().st_mtime).isoformat()
            reports.append(item)

    reports.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return reports


def load_report_payload(
    report_id: str,
    reports_dir: Path,
    workspace_manager=None,
) -> Optional[Dict[str, Any]]:
    """根据 report_id 从传统目录和工作区查找报告。"""
    for report_file in reports_dir.rglob("*.json"):
        with open(report_file, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if payload.get("report_id") == report_id:
            return payload

    if workspace_manager is None:
        return None

    for session_id in workspace_manager.list_sessions():
        for report_file in workspace_manager.get_report_files(session_id):
            with open(report_file, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
            if payload.get("report_id") == report_id:
                return payload
    return None


def resolve_report_owner(payload: Dict[str, Any], workspace_manager=None) -> Optional[str]:
    """从报告 payload 或工作区元数据反查报告所属老年人。"""
    if not isinstance(payload, dict):
        return None

    user_id = payload.get("user_id")
    if user_id:
        return str(user_id)

    session_id = payload.get("session_id")
    if not session_id or workspace_manager is None:
        return None

    metadata = workspace_manager.get_session_metadata(str(session_id))
    owner_id = metadata.get("user_id") if metadata else None
    return str(owner_id) if owner_id else None
