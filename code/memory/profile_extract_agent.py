"""
用户画像信息提取 Agent
从用户的自然语言对话中抽取结构化的 UserProfile 字段
"""

import json
import os
import sys
from typing import Dict, Any, List, Optional

from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)


# -----------------------------------------------------------------------------
# 字段元数据：每个字段的中文描述、合法取值、提取提示
# -----------------------------------------------------------------------------

FIELD_META = {
    # 基本信息
    "age":              {"zh": "年龄", "hint": "数字，如 82"},
    "sex":              {"zh": "性别", "hint": "男 / 女"},
    "province":         {"zh": "省份/地区", "hint": "如 北京、河南、广东"},
    "residence":        {"zh": "居住地类型", "hint": "城市 / 农村"},
    "education_years":  {"zh": "受教育年限", "hint": "数字，如 6（小学）、0（未受教育）"},
    "marital_status":   {"zh": "婚姻状况", "hint": "已婚 / 丧偶 / 离婚 / 未婚"},

    # 健康限制
    "health_limitation": {
        "zh": "过去6个月是否因健康问题限制了日常活动",
        "hint": "否 / 是，有些限制 / 是，严重限制"
    },

    # BADL
    "badl_bathing":      {"zh": "洗澡能力", "hint": "不需要帮助 / 部分帮助 / 多方面帮助"},
    "badl_dressing":     {"zh": "穿衣能力", "hint": "不需要帮助 / 部分帮助 / 多方面帮助"},
    "badl_toileting":    {"zh": "上厕所能力", "hint": "不需要帮助 / 部分帮助 / 多方面帮助"},
    "badl_transferring": {"zh": "室内活动（起床、坐椅）能力", "hint": "不需要帮助 / 部分帮助 / 多方面帮助"},
    "badl_continence":   {"zh": "大小便控制能力", "hint": "不需要帮助 / 部分帮助 / 多方面帮助"},
    "badl_eating":       {"zh": "吃饭能力", "hint": "不需要帮助 / 部分帮助 / 多方面帮助"},

    # IADL
    "iadl_visiting":   {"zh": "串门/走亲访友能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_shopping":   {"zh": "购物能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_cooking":    {"zh": "做饭能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_laundry":    {"zh": "洗衣能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_walking":    {"zh": "步行1公里能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_carrying":   {"zh": "提重物（约5kg）能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_crouching":  {"zh": "蹲下站起能力", "hint": "能 / 有点困难 / 不能做"},
    "iadl_transport":  {"zh": "乘坐公共交通能力", "hint": "能 / 有点困难 / 不能做"},

    # 慢性病
    "hypertension":   {"zh": "高血压", "hint": "是 / 否"},
    "diabetes":       {"zh": "糖尿病", "hint": "是 / 否"},
    "heart_disease":  {"zh": "心脏病", "hint": "是 / 否"},
    "stroke":         {"zh": "中风/脑卒中", "hint": "是 / 否"},
    "cataract":       {"zh": "白内障", "hint": "是 / 否"},
    "cancer":         {"zh": "癌症/恶性肿瘤", "hint": "是 / 否"},
    "arthritis":      {"zh": "关节炎/风湿", "hint": "是 / 否"},

    # 认知功能
    "cognition_time":   {"zh": "时间定向（知道今天是几号）", "hint": "正确 / 错误 / 不知道"},
    "cognition_month":  {"zh": "月份定向（知道现在是几月份）", "hint": "正确 / 错误 / 不知道"},
    "cognition_season": {"zh": "季节定向（知道现在是什么季节）", "hint": "正确 / 错误 / 不知道"},
    "cognition_place":  {"zh": "地点定向（知道自己在哪里）", "hint": "正确 / 错误 / 不知道"},
    "cognition_calc":   {
        "zh": "计算能力（100连续减7，做3次）",
        "hint": "list，3个值各为 正确/错误，如 ['正确','正确','错误']"
    },
    "cognition_draw":   {"zh": "画图能力（能否照样画一个简单图形）", "hint": "正确 / 错误"},

    # 心理状态
    "depression":  {"zh": "是否有抑郁感/情绪低落", "hint": "从不 / 很少 / 有时 / 经常"},
    "anxiety":     {"zh": "是否有焦虑感", "hint": "从不 / 很少 / 有时 / 经常"},
    "loneliness":  {"zh": "是否感到孤独", "hint": "从不 / 很少 / 有时 / 经常"},

    # 生活方式
    "smoking":       {"zh": "吸烟情况", "hint": "从不 / 已戒 / 偶尔 / 每天"},
    "drinking":      {"zh": "饮酒情况", "hint": "从不 / 已戒 / 偶尔 / 每天"},
    "exercise":      {"zh": "锻炼情况", "hint": "从不 / 很少 / 有时 / 经常"},
    "sleep_quality": {"zh": "睡眠质量", "hint": "很好 / 好 / 一般 / 差 / 很差"},

    # 生理指标
    "weight":  {"zh": "体重（公斤）", "hint": "数字，如 55"},
    "height":  {"zh": "身高（厘米）", "hint": "数字，如 160"},
    "vision":  {"zh": "视力情况", "hint": "好 / 一般 / 差"},
    "hearing": {"zh": "听力情况", "hint": "好 / 一般 / 差"},

    # 社会支持
    "living_arrangement": {
        "zh": "居住安排",
        "hint": "独居 / 与配偶同住 / 与子女同住 / 与配偶及子女同住 / 养老院"
    },
    "cohabitants":      {"zh": "同住人数（不含本人）", "hint": "数字，如 2"},
    "financial_status": {"zh": "经济状况自评", "hint": "很好 / 好 / 一般 / 差 / 很差"},
    "income":           {"zh": "年收入（元）", "hint": "数字，如 12000，不知道可留空"},
    "medical_insurance":{"zh": "医疗保险类型", "hint": "如 城镇职工医保 / 城乡居民医保 / 新农合 / 无"},
    "caregiver":        {"zh": "主要照护者", "hint": "如 子女 / 配偶 / 保姆 / 无人照护"},
}


# 提问模板：每个分组问哪些问题（追问时用自然语言一次问一组）
QUESTION_GROUPS = [
    {
        "group_id": "G1",
        "group_name": "基本信息",
        "fields": ["age", "sex", "province", "residence", "education_years", "marital_status"],
        "question": (
            "先来了解一下基本情况吧😊 老人家今年多大了？是男是女？"
            "住在哪个省或城市？是城市还是农村？上过几年学？"
            "现在的婚姻状况是怎样（在婚、丧偶还是其他）？"
        ),
    },
    {
        "group_id": "G2",
        "group_name": "健康限制",
        "fields": ["health_limitation"],
        "question": (
            "过去这半年里，有没有因为身体健康的原因，导致日常活动受到限制？"
            "比如出门、做家务这类事情——没有限制、有一点限制、还是限制比较严重？"
        ),
    },
    {
        "group_id": "G3",
        "group_name": "日常活动（BADL）",
        "fields": [
            "badl_bathing", "badl_dressing", "badl_toileting",
            "badl_transferring", "badl_continence", "badl_eating"
        ],
        "question": (
            "接下来问一下日常生活的基本动作，每项选：不需要帮助、需要部分帮助、还是需要很多帮助👇\n"
            "① 洗澡  ② 穿衣  ③ 上厕所  ④ 室内走动（起床、坐椅子这类）  ⑤ 大小便控制  ⑥ 吃饭\n"
            "您可以直接说，比如'洗澡需要帮助，其他都能自己来'"
        ),
    },
    {
        "group_id": "G4",
        "group_name": "日常活动（IADL）",
        "fields": [
            "iadl_visiting", "iadl_shopping", "iadl_cooking", "iadl_laundry",
            "iadl_walking", "iadl_carrying", "iadl_crouching", "iadl_transport"
        ],
        "question": (
            "再问一些稍微复杂一点的日常活动，每项选：能做、有点困难、还是做不了👇\n"
            "① 串门/走亲戚  ② 买东西  ③ 做饭  ④ 洗衣服  "
            "⑤ 走1公里路  ⑥ 提约5斤重的东西  ⑦ 蹲下再站起来  ⑧ 坐公共交通\n"
            "可以逐一说，也可以总结说，比如'走远路和提重物有点困难，其他基本都能做'"
        ),
    },
    {
        "group_id": "G5",
        "group_name": "慢性病情况",
        "fields": [
            "hypertension", "diabetes", "heart_disease", "stroke",
            "cataract", "cancer", "arthritis"
        ],
        "question": (
            "有没有被医生诊断过这些病？每项说有还是没有就好：\n"
            "高血压、糖尿病、心脏病、中风（脑卒中）、白内障、癌症、关节炎/风湿"
        ),
    },
    {
        "group_id": "G6",
        "group_name": "认知功能",
        "fields": [
            "cognition_time", "cognition_month", "cognition_season",
            "cognition_place", "cognition_calc", "cognition_draw"
        ],
        "question": (
            "现在问一些脑子灵活度的问题，如实回答就好😊\n"
            "① 知道今天是几号吗？② 知道现在是几月份？"
            "③ 知道现在是什么季节？④ 知道自己在哪里？\n"
            "⑤ 100减7等于几？再减7呢？再减一次呢？（三道减法题）\n"
            "⑥ 能照着样子画一个简单的图形吗（比如重叠的五边形）？"
        ),
    },
    {
        "group_id": "G7",
        "group_name": "心理状态",
        "fields": ["depression", "anxiety", "loneliness"],
        "question": (
            "心情方面问几个问题，选从不、很少、有时、还是经常：\n"
            "① 会不会感到情绪低落、提不起劲儿？\n"
            "② 会不会感到紧张焦虑？\n"
            "③ 会不会感到孤独？"
        ),
    },
    {
        "group_id": "G8",
        "group_name": "生活方式",
        "fields": ["smoking", "drinking", "exercise", "sleep_quality"],
        "question": (
            "生活习惯方面：\n"
            "① 吸烟吗？（从不/已戒/偶尔/每天）\n"
            "② 喝酒吗？（从不/已戒/偶尔/每天）\n"
            "③ 平时有锻炼吗？（从不/很少/有时/经常）\n"
            "④ 睡眠质量怎么样？（很好/好/一般/差/很差）"
        ),
    },
    {
        "group_id": "G9",
        "group_name": "身体指标",
        "fields": ["weight", "height", "vision", "hearing"],
        "question": (
            "身体方面：\n"
            "① 大概多重（公斤）？多高（厘米）？\n"
            "② 视力怎么样？（好/一般/差）\n"
            "③ 听力怎么样？（好/一般/差）"
        ),
    },
    {
        "group_id": "G10",
        "group_name": "社会支持",
        "fields": [
            "living_arrangement", "cohabitants", "financial_status",
            "income", "medical_insurance", "caregiver"
        ],
        "question": (
            "最后了解一下家庭和支持情况：\n"
            "① 现在和谁住在一起？（独居/和老伴/和子女/和老伴及子女/住养老院）\n"
            "② 同住有几个人（不算自己）？\n"
            "③ 经济状况自己觉得怎么样？（很好/好/一般/差/很差）\n"
            "④ 有没有医保？是什么类型的？\n"
            "⑤ 平时主要是谁在照顾？（子女/老伴/保姆/自己/无人）"
        ),
    },
]

# 快速查找：字段名 -> 所在分组ID
FIELD_TO_GROUP: Dict[str, str] = {}
for _g in QUESTION_GROUPS:
    for _f in _g["fields"]:
        FIELD_TO_GROUP[_f] = _g["group_id"]


# -----------------------------------------------------------------------------
# ProfileExtractAgent
# -----------------------------------------------------------------------------

class ProfileExtractAgent:
    """
    从用户对话中抽取 UserProfile 字段
    返回 {字段名: 值} 的 dict，供 UserProfileStore.update_profile 使用
    """

    SYSTEM_PROMPT = (
        "你是一个专门从对话中提取老年人健康信息的助手。\n\n"
        "你的任务：\n"
        "- 仔细阅读用户的最新回答和当前正在收集的字段列表\n"
        "- 从用户回答中提取对应字段的值，严格按照每个字段的合法取值映射\n"
        "- 只提取确定能从文本中识别出来的信息，不要猜测或补全\n"
        "- 返回 JSON 格式，key 为字段英文名，value 为提取到的值\n\n"
        "字段合法取值说明（必须映射到这些标准值）：\n"
        "- BADL类：不需要帮助 / 部分帮助 / 多方面帮助\n"
        "- IADL类：能 / 有点困难 / 不能做\n"
        "- 慢性病类：是 / 否\n"
        "- 认知定向类：正确 / 错误 / 不知道\n"
        "- cognition_calc：list，3个值，每个为 正确/错误\n"
        "- 心理状态类：从不 / 很少 / 有时 / 经常\n"
        "- 生活方式-吸烟/饮酒：从不 / 已戒 / 偶尔 / 每天\n"
        "- 锻炼：从不 / 很少 / 有时 / 经常\n"
        "- 睡眠质量：很好 / 好 / 一般 / 差 / 很差\n"
        "- 视力/听力：好 / 一般 / 差\n"
        "- 性别：男 / 女\n"
        "- 居住地：城市 / 农村\n"
        "- 健康限制：否 / 是，有些限制 / 是，严重限制\n\n"
        "注意：\n"
        "- age、education_years、cohabitants 提取为整数\n"
        "- weight、height、income 提取为浮点数\n"
        "- 如果用户说都能自己来之类的，对应BADL全部填不需要帮助，IADL全部填能\n"
        "- 如果用户说某病有，填是；说没有填否\n"
        "- 只返回 JSON，不要有其他文字"
    )

    def extract(
        self,
        user_message: str,
        target_fields: List[str],
        conversation_history: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        从用户消息中提取字段值

        Args:
            user_message: 用户最新的一条消息
            target_fields: 当前需要提取的字段列表
            conversation_history: 最近几轮对话上下文（可选，帮助理解语境）

        Returns:
            {字段名: 值} dict，只包含成功提取的字段
        """
        # 构造字段说明
        field_desc_lines = []
        for f in target_fields:
            meta = FIELD_META.get(f, {})
            zh = meta.get("zh", f)
            hint = meta.get("hint", "")
            field_desc_lines.append(f"- {f}（{zh}）：{hint}")
        fields_text = "\n".join(field_desc_lines)

        # 构造对话上下文（最近3轮）
        context_text = ""
        if conversation_history:
            recent = conversation_history[-6:]
            for msg in recent:
                role_zh = "用户" if msg["role"] == "user" else "助手"
                context_text += f"{role_zh}：{msg['content']}\n"

        user_prompt = (
            "【需要提取的字段】\n"
            f"{fields_text}\n\n"
            "【最近对话上下文】\n"
            f"{context_text if context_text else '（无）'}\n\n"
            "【用户最新回答】\n"
            f"{user_message}\n\n"
            "请提取上面字段的值，返回 JSON。如果某个字段用户没有提到，就不要包含在 JSON 里。"
        )

        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=800,
                response_format={"type": "json_object"}
            )
            raw = response.choices[0].message.content.strip()
            extracted = json.loads(raw)
            # 过滤掉不在 target_fields 中的 key（防止幻觉）
            return {k: v for k, v in extracted.items() if k in target_fields or k == "cognition_calc"}
        except Exception as e:
            print(f"[ProfileExtractAgent] 提取失败: {e}")
            return {}

    def generate_followup(
        self,
        missing_fields: List[str],
        conversation_history: Optional[List[Dict]] = None,
        group_question: Optional[str] = None
    ) -> str:
        """
        针对缺失字段生成口语化的追问话术

        Args:
            missing_fields: 还缺的字段名列表
            conversation_history: 最近对话上下文
            group_question: 当前分组的预设问题模板（优先使用，省一次LLM调用）

        Returns:
            追问话术字符串
        """
        # 如果有预设模板就直接用
        if group_question:
            return group_question

        # 否则用 LLM 动态生成追问
        field_desc_lines = []
        for f in missing_fields[:8]:
            meta = FIELD_META.get(f, {})
            zh = meta.get("zh", f)
            hint = meta.get("hint", "")
            field_desc_lines.append(f"- {zh}（{hint}）")

        context_text = ""
        if conversation_history:
            recent = conversation_history[-4:]
            for msg in recent:
                role_zh = "用户" if msg["role"] == "user" else "助手"
                context_text += f"{role_zh}：{msg['content']}\n"

        prompt = (
            "你是一个帮助收集老年人健康信息的助手，语气要亲切口语化，像家人朋友那样问。\n\n"
            "【还需要收集的信息】\n"
            f"{''.join(field_desc_lines)}\n\n"
            "【最近对话】\n"
            f"{context_text if context_text else '（刚开始对话）'}\n\n"
            "请把上面需要收集的信息，用自然、亲切的方式组合成一个追问，不要像问卷一样生硬。\n"
            "直接输出问话内容，不要加任何前缀。"
        )

        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            # fallback：直接列出缺的字段
            items = [FIELD_META.get(f, {}).get("zh", f) for f in missing_fields[:6]]
            return f"还需要了解一下：{'、'.join(items)}，您方便说一下吗？"
