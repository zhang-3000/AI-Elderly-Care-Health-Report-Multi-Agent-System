"""
报告生成与存储辅助函数。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from multi_agent_system_v2 import UserProfile


def profile_to_dict(profile: UserProfile) -> Dict[str, Any]:
    """将 UserProfile 转为可持久化字典。"""
    payload = asdict(profile)
    payload.pop("user_type", None)
    return payload


def save_report_bundle(
    reports_dir: Path,
    workspace_manager,
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    保存报告到传统目录与工作区，返回完整 JSON 载荷。
    """
    timestamp = datetime.now()
    report_id = timestamp.strftime("%Y%m%d_%H%M%S")

    age = profile.get("age", "未知")
    sex = profile.get("sex", "未知")

    date_dir = reports_dir / timestamp.strftime("%Y%m")
    date_dir.mkdir(parents=True, exist_ok=True)

    base_filename = f"report_{report_id}_{age}岁{sex}"
    payload = {
        "report_id": report_id,
        "session_id": session_id,
        "generated_at": timestamp.isoformat(),
        "profile": profile,
        "raw_results": results,
        "report_data": report_data,
    }

    json_file = date_dir / f"{base_filename}.json"
    with open(json_file, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)

    markdown_file = date_dir / f"{base_filename}.md"
    markdown_content = generate_markdown_report(profile, results, report_data, timestamp)
    with open(markdown_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(markdown_content)

    if session_id and workspace_manager is not None:
        workspace_manager.save_report(session_id, payload, "json")
        workspace_manager.save_report(session_id, markdown_content, "md")
        workspace_manager.update_metadata(session_id, {"has_report": True})

    return payload


def generate_markdown_report(
    profile: Dict[str, Any],
    results: Dict[str, Any],
    report_data: Dict[str, Any],
    timestamp: datetime,
) -> str:
    """生成 Markdown 格式的健康报告。"""
    status = results.get("status", {})
    risk = results.get("risk", {})
    raw_report = results.get("report", "")

    md_lines = [
        "# 养老健康评估报告",
        "",
        "## 报告信息",
        "",
        f"- **生成时间**: {timestamp.strftime('%Y年%m月%d日 %H:%M')}",
        f"- **年龄**: {profile.get('age', '未知')}岁",
        f"- **性别**: {profile.get('sex', '未知')}",
        "",
        "## 1. 健康报告总结",
        "",
    ]

    if raw_report:
        summary_match = re.search(r"##\s*1\.\s*健康报告总结\s*(.+?)(?:\n##\s|\Z)", raw_report, re.S)
        if summary_match:
            md_lines.append(summary_match.group(1).strip())
        else:
            md_lines.append(report_data.get("summary", "暂无总结"))
    else:
        md_lines.append(report_data.get("summary", "暂无总结"))
    md_lines.append("")

    md_lines.extend(
        [
            "## 2. 功能状态评估",
            "",
            f"**状态描述**: {status.get('status_description', '无')}",
            "",
        ]
    )

    health_portrait = report_data.get("healthPortrait", {})
    if health_portrait:
        md_lines.extend(
            [
                "### 健康画像",
                "",
                f"**功能状态**: {health_portrait.get('functionalStatus', '无描述')}",
                "",
            ]
        )

        strengths = health_portrait.get("strengths", [])
        if strengths:
            md_lines.append("**优势**:")
            md_lines.extend([f"- {item}" for item in strengths])
            md_lines.append("")

        problems = health_portrait.get("problems", [])
        if problems:
            md_lines.append("**需要关注的问题**:")
            md_lines.extend([f"- {item}" for item in problems])
            md_lines.append("")

    md_lines.extend(["## 3. 风险预测分析", ""])
    risk_factors = report_data.get("riskFactors", {})
    for label, items in [("短期风险（1-4周）", risk_factors.get("shortTerm", [])), ("中期风险（1-6月）", risk_factors.get("midTerm", []))]:
        if not items:
            continue
        md_lines.extend([f"### {label}", ""])
        for item in items:
            md_lines.extend(
                [
                    f"#### {item['name']}",
                    f"- **风险等级**: {item['level']}",
                    f"- **时间范围**: {item['timeframe']}",
                    f"- **描述**: {item['description']}",
                    "",
                ]
            )

    if risk:
        md_lines.extend(
            [
                "**风险总结**:",
                f"- 短期风险数: {len(risk_factors.get('shortTerm', []))}项",
                f"- 中期风险数: {len(risk_factors.get('midTerm', []))}项",
                f"- 风险概况: {risk.get('risk_summary', '无')}",
                "",
            ]
        )

    md_lines.extend(["## 4. 行动建议", ""])
    recommendations = report_data.get("recommendations", {})
    for section_title, items in [
        ("优先级 A - 立即执行", recommendations.get("priority1", [])),
        ("优先级 B - 本周完成", recommendations.get("priority2", [])),
        ("优先级 C - 后续跟进", recommendations.get("priority3", [])),
    ]:
        if not items:
            continue
        md_lines.extend([f"### {section_title}", ""])
        for item in items:
            md_lines.extend(
                [
                    f"#### {item['title']}",
                    f"- **类别**: {item['category']}",
                    f"- **描述**: {item['description']}",
                    "",
                ]
            )

    if raw_report:
        md_lines.extend(["## 5. 完整评估报告", "", raw_report, ""])

    md_lines.extend(
        [
            "---",
            "",
            "*本报告由 AI 养老健康助手自动生成，仅供参考。请结合专业医生的诊断和建议。*",
        ]
    )
    return "\n".join(md_lines)
