from __future__ import annotations

import re
from dataclasses import fields
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from multi_agent_system_v2 import UserProfile


BADL_MAP = {
    0: "不需要帮助",
    1: "需要别人搭把手",
    2: "大部分要靠别人帮忙",
    3: "大部分要靠别人帮忙",
}

IADL_MAP = {
    0: "能自己做",
    1: "做起来有点困难",
    2: "现在做不了",
    3: "现在做不了",
}

GENDER_MAP = {
    "male": "男",
    "female": "女",
}

RESIDENCE_MAP = {
    "city": "城市",
    "urban": "城市",
    "rural": "农村",
}

LIVING_MAP = {
    "alone": "独居",
    "with_spouse": "和老伴",
    "with_children": "和子女",
    "nursing_home": "住养老院",
}

LIFESTYLE_MAP = {
    "smoking": {
        "never": "从不",
        "former": "已戒",
        "current": "每天",
    },
    "drinking": {
        "never": "从不",
        "occasional": "偶尔",
        "regular": "每天",
    },
    "exercise": {
        "none": "从不",
        "occasional": "有时",
        "regular": "经常",
    },
    "sleep": {
        "good": "好",
        "fair": "一般",
        "poor": "差",
    },
}

MOOD_MAP = {
    "normal": "从不",
    "depression": "有时",
    "anxiety": "有时",
    "both": "经常",
}

COGNITION_MAP = {
    "normal": "正确",
    "mild_impairment": "错误",
    "moderate_impairment": "错误",
    "severe_impairment": "不知道",
}

VISION_HEARING_MAP = {
    "good": "好",
    "fair": "一般",
    "poor": "差",
}

CHRONIC_FIELDS = {
    "hypertension": "hypertension",
    "diabetes": "diabetes",
    "heart_disease": "coronary_heart_disease",
    "stroke": "stroke",
    "cancer": "cancer",
    "arthritis": "arthritis",
}


def _to_int(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return value


def _to_float(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return value


def _map_score(value: Any, mapping: Dict[int, str]) -> Any:
    if value in (None, ""):
        return None
    try:
        score = int(value)
        return mapping.get(score, None)
    except Exception:
        text = str(value).strip()
        return text if text else None


def _get_by_path(obj: Dict[str, Any], path: Tuple[str, ...], default: Any = None) -> Any:
    current: Any = obj
    for part in path:
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return current


def _as_backend_direct(raw: Dict[str, Any]) -> UserProfile:
    valid = {f.name for f in fields(UserProfile)}
    payload = {k: v for k, v in raw.items() if k in valid}
    return UserProfile(**payload)


def _from_frontend_profile(raw: Dict[str, Any]) -> UserProfile:
    demographics = raw.get("demographics", {})
    functional_status = raw.get("functionalStatus", {})
    badl = functional_status.get("badl", {})
    iadl = functional_status.get("iadl", {})
    health = raw.get("healthFactors", {})
    lifestyle = raw.get("lifestyle", {})
    support = raw.get("socialSupport", {})

    diseases = set(health.get("chronicDiseases") or [])

    return UserProfile(
        age=_to_int(demographics.get("age")),
        sex=GENDER_MAP.get(demographics.get("gender"), demographics.get("gender")),
        residence=RESIDENCE_MAP.get(str(demographics.get("livingStatus", "")).lower(), None),
        education_years=demographics.get("education") or None,
        marital_status=demographics.get("maritalStatus") or None,
        health_limitation=None,
        badl_bathing=_map_score(badl.get("bathing"), BADL_MAP),
        badl_dressing=_map_score(badl.get("dressing"), BADL_MAP),
        badl_toileting=_map_score(badl.get("toileting"), BADL_MAP),
        badl_transferring=_map_score(badl.get("transfer"), BADL_MAP),
        badl_continence=_map_score(badl.get("continence"), BADL_MAP),
        badl_eating=_map_score(badl.get("feeding"), BADL_MAP),
        iadl_visiting=_map_score(iadl.get("visiting"), IADL_MAP),
        iadl_shopping=_map_score(iadl.get("shopping"), IADL_MAP),
        iadl_cooking=_map_score(iadl.get("cooking"), IADL_MAP),
        iadl_laundry=_map_score(iadl.get("washing"), IADL_MAP),
        iadl_walking=_map_score(iadl.get("walking"), IADL_MAP),
        iadl_carrying=_map_score(iadl.get("lifting"), IADL_MAP),
        iadl_crouching=_map_score(iadl.get("crouching"), IADL_MAP),
        iadl_transport=_map_score(iadl.get("transport"), IADL_MAP),
        hypertension="是" if "hypertension" in diseases else "否",
        diabetes="是" if "diabetes" in diseases else "否",
        coronary_heart_disease="是" if "heart_disease" in diseases else "否",
        stroke="是" if "stroke" in diseases else "否",
        cataract="否",
        cancer="是" if "cancer" in diseases else "否",
        arthritis="是" if "arthritis" in diseases else "否",
        cognition_time=COGNITION_MAP.get(health.get("cognition"), None),
        cognition_month=COGNITION_MAP.get(health.get("cognition"), None),
        cognition_season=COGNITION_MAP.get(health.get("cognition"), None),
        cognition_place=COGNITION_MAP.get(health.get("cognition"), None),
        cognition_calc=None,
        depression=MOOD_MAP.get(health.get("mood"), None),
        anxiety=MOOD_MAP.get(health.get("mood"), None),
        loneliness=MOOD_MAP.get(health.get("mood"), None),
        smoking=LIFESTYLE_MAP["smoking"].get(lifestyle.get("smoking"), None),
        drinking=LIFESTYLE_MAP["drinking"].get(lifestyle.get("drinking"), None),
        exercise=LIFESTYLE_MAP["exercise"].get(lifestyle.get("exercise"), None),
        sleep_quality=LIFESTYLE_MAP["sleep"].get(lifestyle.get("sleep"), None),
        weight=None,
        height=None,
        vision=VISION_HEARING_MAP.get(health.get("vision"), None),
        hearing=VISION_HEARING_MAP.get(health.get("hearing"), None),
        living_arrangement=LIVING_MAP.get(demographics.get("livingStatus"), None),
        financial_status=None,
        medical_insurance=None,
        caregiver=support.get("primaryCaregiver") or None,
        user_type="elderly",
    )


def to_backend_profile(raw: Dict[str, Any]) -> UserProfile:
    if not isinstance(raw, dict):
        return UserProfile()

    if "demographics" in raw and "functionalStatus" in raw:
        return _from_frontend_profile(raw)

    return _as_backend_direct(raw)


def _severity_to_level(text: Any) -> str:
    value = str(text or "").lower()
    if "高" in value or "high" in value:
        return "high"
    if "低" in value or "low" in value:
        return "low"
    return "medium"


def _extract_summary_from_markdown(report_text: str) -> str:
    if not report_text:
        return ""
    section = re.search(r"##\s*1\.\s*健康报告总结\s*(.+?)(?:\n##\s|\Z)", report_text, re.S)
    if not section:
        return ""
    content = section.group(1)
    lines = [line.strip(" -*\t") for line in content.splitlines() if line.strip()]
    return " ".join(lines[:3]).strip()


def _problem_to_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        problem = item.get("problem", "")
        impact = item.get("impact", "")
        if problem and impact:
            return f"{problem}: {impact}"
        return str(problem or impact or "")
    return str(item)


def _map_short_term_risks(risks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for risk in risks or []:
        desc_parts = [risk.get("trigger"), risk.get("prevention_key")]
        description = "；".join([p for p in desc_parts if p]) or "暂无描述"
        items.append(
            {
                "name": risk.get("risk", "未命名风险"),
                "level": _severity_to_level(risk.get("severity")),
                "description": description,
                "timeframe": risk.get("timeframe", "1-4周"),
            }
        )
    return items


def _map_mid_term_risks(risks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for risk in risks or []:
        desc_parts = [risk.get("chain"), risk.get("prevention_key")]
        description = "；".join([p for p in desc_parts if p]) or "暂无描述"
        items.append(
            {
                "name": risk.get("risk", "未命名风险"),
                "level": _severity_to_level(risk.get("severity")),
                "description": description,
                "timeframe": risk.get("timeframe", "1-6月"),
            }
        )
    return items


def _map_recommendation(action: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
    action_id = str(action.get("action_id") or action.get("id") or "")
    title = action.get("title") or "未命名建议"
    description = "；".join(
        [
            str(action.get("subtitle") or "").strip(),
            str(action.get("completion_criteria") or "").strip(),
            str(reason or "").strip(),
        ]
    )
    description = "；".join([p for p in description.split("；") if p]) or "请按医护建议执行"

    return {
        "id": action_id or f"rec_{abs(hash(title)) % 100000}",
        "title": title,
        "description": description,
        "category": action.get("category", "健康管理"),
        "completed": False,
    }


def _map_recommendations(results: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    actions_payload = results.get("actions") if isinstance(results.get("actions"), dict) else {}
    action_list = actions_payload.get("actions") if isinstance(actions_payload, dict) else []
    if not isinstance(action_list, list):
        action_list = []

    action_by_id = {
        str(item.get("action_id")): item
        for item in action_list
        if isinstance(item, dict) and item.get("action_id")
    }

    priority_payload = results.get("priority") if isinstance(results.get("priority"), dict) else {}

    def _collect(priority_key: str) -> List[Dict[str, Any]]:
        output: List[Dict[str, Any]] = []
        for item in priority_payload.get(priority_key, []) or []:
            if not isinstance(item, dict):
                continue
            action = action_by_id.get(str(item.get("action_id")))
            if action:
                output.append(_map_recommendation(action, reason=str(item.get("reason") or "")))
        return output

    p1 = _collect("priority_a")
    p2 = _collect("priority_b")
    p3 = _collect("priority_c")

    if not any([p1, p2, p3]) and action_list:
        for idx, action in enumerate(action_list):
            if not isinstance(action, dict):
                continue
            mapped = _map_recommendation(action)
            if idx < 3:
                p1.append(mapped)
            elif idx < 7:
                p2.append(mapped)
            else:
                p3.append(mapped)

    return {
        "priority1": p1,
        "priority2": p2,
        "priority3": p3,
    }


def to_frontend_report_data(results: Dict[str, Any]) -> Dict[str, Any]:
    status = results.get("status") if isinstance(results.get("status"), dict) else {}
    risk = results.get("risk") if isinstance(results.get("risk"), dict) else {}
    factors = results.get("factors") if isinstance(results.get("factors"), dict) else {}

    summary = _extract_summary_from_markdown(str(results.get("report") or ""))
    if not summary:
        summary = "；".join(
            [
                str(status.get("status_description") or ""),
                str(risk.get("risk_summary") or ""),
            ]
        )
        summary = "；".join([p for p in summary.split("；") if p]) or "已生成健康评估与照护行动计划。"

    health_portrait = {
        "functionalStatus": str(
            _get_by_path(factors, ("functional_status", "description"), "")
            or status.get("status_description")
            or "暂无功能状态描述"
        ),
        "strengths": factors.get("strengths") if isinstance(factors.get("strengths"), list) else [],
        "problems": [
            _problem_to_text(item)
            for item in (factors.get("main_problems") if isinstance(factors.get("main_problems"), list) else [])
            if _problem_to_text(item)
        ],
    }

    short_term = _map_short_term_risks(risk.get("short_term_risks") if isinstance(risk, dict) else [])
    mid_term = _map_mid_term_risks(risk.get("medium_term_risks") if isinstance(risk, dict) else [])

    return {
        "summary": summary,
        "healthPortrait": health_portrait,
        "riskFactors": {
            "shortTerm": short_term,
            "midTerm": mid_term,
        },
        "recommendations": _map_recommendations(results),
        "generatedAt": datetime.now().isoformat(),
    }
