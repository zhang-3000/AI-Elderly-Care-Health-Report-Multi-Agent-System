"""
评测模块共享工具函数。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ── LLM 配置（复用主系统的 DeepSeek 配置）──────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "180"))
LLM_MAX_RETRIES = int(os.getenv("DEEPSEEK_MAX_RETRIES", "2"))

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # 清除可能干扰的代理环境变量
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(key, None)
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def call_llm(
    prompt: str,
    system_prompt: str = "你是一个严谨的评测助手，请按照指令精确完成任务。",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> str:
    """调用 DeepSeek LLM，返回文本回复。"""
    client = _get_client()
    total_attempts = LLM_MAX_RETRIES + 1

    for attempt in range(1, total_attempts + 1):
        try:
            logger.info(
                "[eval] LLM call attempt=%s/%s prompt_chars=%s",
                attempt,
                total_attempts,
                len(prompt),
            )
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=LLM_TIMEOUT,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("[eval] LLM call failed attempt=%s: %s", attempt, e)
            if attempt < total_attempts:
                time.sleep(2.0)
            else:
                raise


# ── 文本处理 ────────────────────────────────────────────────────────


def split_chinese_sentences(text: str) -> List[str]:
    """按中文句号/叹号/问号分句，过滤空白和过短片段。"""
    parts = re.split(r"[。！？\n]", text)
    sentences = [s.strip() for s in parts if s.strip() and len(s.strip()) > 5]
    return sentences


def parse_json_response(text: str) -> Any:
    """尝试从 LLM 回复中提取 JSON，兼容 markdown code block。"""
    # 尝试提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.S)
    if match:
        return json.loads(match.group(1))
    # 直接尝试解析
    return json.loads(text)


# ── Profile 要素提取 ────────────────────────────────────────────────

# 慢性病字段映射
_DISEASE_FIELDS = {
    "hypertension": "高血压",
    "diabetes": "糖尿病",
    "heart_disease": "心脏病",
    "stroke": "中风/脑卒中",
    "arthritis": "关节炎",
    "cancer": "肿瘤/癌症",
}

# BADL 字段映射
_BADL_FIELDS = {
    "badl_bathing": "洗澡",
    "badl_dressing": "穿衣",
    "badl_toileting": "上厕所",
    "badl_transferring": "室内移动",
    "badl_continence": "大小便控制",
    "badl_eating": "吃饭",
}

# IADL 字段映射
_IADL_FIELDS = {
    "iadl_visiting": "串门",
    "iadl_shopping": "买东西",
    "iadl_cooking": "做饭",
    "iadl_laundry": "洗衣服",
    "iadl_walking": "走1公里路",
    "iadl_carrying": "提5公斤重物",
    "iadl_crouching": "蹲下站起",
    "iadl_transport": "乘坐公共交通",
}


def _is_positive(value: Any) -> bool:
    """判断字段值是否为"有/是"类的肯定值。"""
    s = str(value).strip().lower()
    return s in {"是", "有", "患有", "1", "true"}


def _is_limited(value: Any) -> bool:
    """判断功能项是否受限（非完全自理）。"""
    s = str(value).strip()
    # 数字编码: "2"=有些困难, "3"=完全不能
    if s in {"2", "3"}:
        return True
    # 明确正常的值
    normal_values = {"不需要帮助", "能", "自己做", "1", "不需要", "无困难"}
    if s in normal_values:
        return False
    # 文本值: 各种"困难"的表述（注意排除"不需要帮助"）
    limited_keywords = ("困难", "做不了", "费劲", "完全不能")
    return any(kw in s for kw in limited_keywords)


def extract_profile_elements(profile: Dict[str, Any]) -> List[str]:
    """
    从用户画像中提取关键要素清单，用于 Profile Coverage 评测。

    返回格式如: ["患有高血压", "洗澡需要帮助", "认知功能正常", ...]
    """
    elements: List[str] = []

    # 基本信息
    age = profile.get("age")
    sex = profile.get("sex")
    if age:
        elements.append(f"年龄{age}岁")
    if sex:
        elements.append(f"性别{sex}")

    # 慢性病
    for field, name in _DISEASE_FIELDS.items():
        value = profile.get(field)
        if value and _is_positive(value):
            elements.append(f"患有{name}")

    # BADL 受限项
    for field, name in _BADL_FIELDS.items():
        value = profile.get(field)
        if value and _is_limited(value):
            elements.append(f"{name}功能受限")

    # IADL 受限项
    for field, name in _IADL_FIELDS.items():
        value = profile.get(field)
        if value and _is_limited(value):
            elements.append(f"{name}有困难")

    # 认知状态
    cognitive_fields = ["cognition_time", "cognition_month", "cognition_season", "cognition_place"]
    for f in cognitive_fields:
        value = profile.get(f)
        if value and str(value).strip() in {"2", "不能", "错误", "不知道"}:
            elements.append("认知功能有下降")
            break

    # 心理状态
    depression = profile.get("depression")
    loneliness = profile.get("loneliness")
    if depression:
        ds = str(depression).strip()
        if ds in {"是", "有", "1", "true", "经常", "总是", "有时"}:
            elements.append("有抑郁倾向")
    if loneliness:
        ls = str(loneliness).strip()
        if ls in {"是", "有", "1", "true", "经常", "总是", "有时"}:
            elements.append("感到孤独")

    # 视力/听力
    vision = profile.get("vision")
    hearing = profile.get("hearing")
    if vision:
        vs = str(vision).strip()
        # "能看见并区分" 表示正常；其他值表示有问题
        if vs not in {"能看见并区分", "1", "正常"} and vs not in {"", "None"}:
            elements.append("视力受损")
    if hearing:
        hs = str(hearing).strip()
        # hearing="否" 在 CLHLS 中表示听力不好（问的是"能否听清"）
        if hs in {"否", "2", "3", "不能", "不好", "差", "听不清"}:
            elements.append("听力受损")

    # 生活习惯
    smoking = profile.get("smoking")
    exercise = profile.get("exercise")
    if smoking and _is_positive(smoking):
        elements.append("有吸烟习惯")
    if exercise:
        s = str(exercise).strip()
        if s in {"1", "从不", "很少"}:
            elements.append("缺乏锻炼")

    return elements


def build_retrieved_context_text(
    knowledge: Dict[str, Any],
    use_full_text: bool = False,
) -> str:
    """
    从 results['knowledge'] 中提取检索上下文文本。

    Args:
        knowledge: results['knowledge'] 字典
        use_full_text: True 使用 chunk 完整 text（适合 Faithfulness 深度验证），
                       False 使用 excerpt（适合 Context Relevance 精准度评估，
                       也与 LLM 实际看到的上下文一致）。
    """
    # 优先使用 combined_context（已由 KnowledgeAgent 格式化好的上下文）
    combined = knowledge.get("combined_context", "")
    if combined and not use_full_text:
        return combined

    texts: List[str] = []
    categories = ["risk_prevention", "disease_management", "functional_training"]

    for category in categories:
        cat_data = knowledge.get(category, {})
        hits = cat_data.get("hits", [])
        for hit in hits:
            if use_full_text:
                content = hit.get("text") or hit.get("excerpt", "")
            else:
                content = hit.get("excerpt", "")
            if content:
                source = hit.get("doc_name", "未知文档")
                title = hit.get("title", "")
                header = f"[来源: {source}"
                if title:
                    header += f" / {title}"
                header += "]"
                texts.append(f"{header}\n{content}")

    return "\n\n---\n\n".join(texts)
