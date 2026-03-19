"""
家属端专用问题组 - Family Caregiver Assessment Questions
针对家属视角设计的评估问题，主语从"您"改为"老人"
"""

from typing import List, Dict, Any

# 家属端问题分组
FAMILY_QUESTION_GROUPS = [
    {
        "group_id": "F0",
        "group_name": "家属基本信息",
        "fields": ["family_relation", "is_primary_caregiver", "living_together", "info_source"],
        "questions": [
            {
                "field": "family_relation",
                "question": "请先告诉我，您和老人是什么关系？",
                "options": ["子女", "配偶", "孙辈", "护工/照护员", "其他亲属", "其他"]
            },
            {
                "field": "is_primary_caregiver",
                "question": "您是老年人的主要照护者吗？",
                "options": ["是", "不是"]
            },
            {
                "field": "living_together",
                "question": "您与老年人同住吗？",
                "options": ["同住，日常经常照顾", "不同住，但经常见面或联系", "偶尔见面"]
            },
            {
                "field": "info_source",
                "question": "您接下来提供的信息，主要是根据您平时观察到的情况，还是老人自己告诉您的情况？",
                "options": ["主要是我观察到的", "主要是老人告诉我的", "两者都有"]
            }
        ]
    },
    {
        "group_id": "F1",
        "group_name": "老人基本信息",
        "fields": ["elderly_age", "elderly_sex", "elderly_province", "elderly_residence"],
        "question": "请告诉我老人的基本情况：年龄、性别、居住地（省份）、是城市还是农村？"
    },
    {
        "group_id": "F2",
        "group_name": "近期变化观察",
        "fields": ["recent_changes"],
        "question": (
            "您最近觉得老人最明显的变化主要在哪些方面？可以多选：\n"
            "① 走路、起身、活动变慢了\n"
            "② 记性变差了\n"
            "③ 情绪不太好\n"
            "④ 吃饭变少了\n"
            "⑤ 睡眠变差了\n"
            "⑥ 容易跌倒或差点跌倒\n"
            "⑦ 看东西、听别人说话更困难了\n"
            "⑧ 没觉得有明显变化"
        )
    },
    {
        "group_id": "F3",
        "group_name": "日常照护需求",
        "fields": ["care_needs_bathing", "care_needs_dressing", "care_needs_toileting", 
                   "care_needs_eating", "care_needs_moving", "care_needs_cooking",
                   "care_needs_shopping", "care_needs_going_out", "care_needs_medication"],
        "question": (
            "目前老人在哪些事情上需要别人帮忙？\n"
            "① 洗澡  ② 穿衣  ③ 上厕所  ④ 吃饭  ⑤ 起床/坐下/走动\n"
            "⑥ 做饭  ⑦ 买东西  ⑧ 出门  ⑨ 吃药\n\n"
            "对于需要帮忙的项目，请说明程度：\n"
            "- 偶尔需要提醒\n"
            "- 经常需要协助\n"
            "- 基本离不开人"
        )
    },
    {
        "group_id": "F4",
        "group_name": "健康问题",
        "fields": ["chronic_diseases", "recent_hospitalization", "falls_last_3months",
                   "incontinence", "weight_loss", "swallowing_difficulty",
                   "mental_state_decline", "cognitive_issues"],
        "questions": [
            {
                "field": "chronic_diseases",
                "question": "老人目前有哪些已知的慢性病？（如高血压、糖尿病、心脏病等）"
            },
            {
                "field": "recent_hospitalization",
                "question": "近期是否住院？如果有，是什么原因？"
            },
            {
                "field": "falls_last_3months",
                "question": "最近3个月有没有跌倒过？"
            },
            {
                "field": "incontinence",
                "question": "是否有大小便失禁的情况？"
            },
            {
                "field": "weight_loss",
                "question": "是否有明显体重下降？"
            },
            {
                "field": "swallowing_difficulty",
                "question": "是否有吞咽困难（吃东西容易呛）？"
            },
            {
                "field": "mental_state_decline",
                "question": "是否近期精神状态差很多？"
            },
            {
                "field": "cognitive_issues",
                "question": "是否近期出现迷路、认错人、重复说话等情况？"
            }
        ]
    },
    {
        "group_id": "F5",
        "group_name": "家属观察 - 睡眠",
        "fields": ["sleep_observation"],
        "question": "您观察到老人最近睡眠有没有明显变差？比如入睡困难、夜里频繁醒来、早醒等。"
    },
    {
        "group_id": "F6",
        "group_name": "家属观察 - 外出能力",
        "fields": ["going_out_safety"],
        "question": "老人现在一个人出门，您放心吗？有没有担心他/她走丢、跌倒或其他安全问题？"
    },
    {
        "group_id": "F7",
        "group_name": "家属观察 - 情绪状态",
        "fields": ["emotional_state"],
        "question": "您觉得老人平时情绪状态怎么样？是否经常情绪低落、焦虑、易怒或孤独？"
    },
    {
        "group_id": "F8",
        "group_name": "家属观察 - 记忆力",
        "fields": ["memory_decline"],
        "question": "您是否感觉老人比以前更容易忘事？比如忘记吃药、忘记关火、重复问同样的问题等。"
    },
    {
        "group_id": "F9",
        "group_name": "用药情况",
        "fields": ["medications", "medication_compliance"],
        "questions": [
            {
                "field": "medications",
                "question": "老人目前在吃哪些药？（请尽量说出药名或用途）"
            },
            {
                "field": "medication_compliance",
                "question": "老人吃药规律吗？会不会忘记吃药或吃错药？"
            }
        ]
    },
    {
        "group_id": "F10",
        "group_name": "照护困难与需求",
        "fields": ["care_challenges", "care_needs"],
        "questions": [
            {
                "field": "care_challenges",
                "question": "在照顾老人的过程中，您觉得最困难或最担心的是什么？"
            },
            {
                "field": "care_needs",
                "question": "您希望在哪些方面得到帮助或建议？（如照护技巧、安全改造、就医指导等）"
            }
        ]
    }
]

# 家属端字段元数据
FAMILY_FIELD_META = {
    # 家属基本信息
    "family_relation": {"zh": "与老人关系", "hint": "子女/配偶/孙辈等"},
    "is_primary_caregiver": {"zh": "是否主要照护者", "hint": "是/否"},
    "living_together": {"zh": "是否同住", "hint": "同住/不同住但常见/偶尔见"},
    "info_source": {"zh": "信息来源", "hint": "观察/转述/两者都有"},
    
    # 老人基本信息
    "elderly_age": {"zh": "老人年龄", "hint": "数字"},
    "elderly_sex": {"zh": "老人性别", "hint": "男/女"},
    "elderly_province": {"zh": "老人居住省份", "hint": "如北京、上海"},
    "elderly_residence": {"zh": "老人居住地类型", "hint": "城市/农村"},
    
    # 近期变化
    "recent_changes": {"zh": "近期明显变化", "hint": "多选：活动/记忆/情绪/饮食/睡眠/跌倒/视听/无变化"},
    
    # 照护需求
    "care_needs_bathing": {"zh": "洗澡需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_dressing": {"zh": "穿衣需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_toileting": {"zh": "如厕需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_eating": {"zh": "吃饭需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_moving": {"zh": "活动需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_cooking": {"zh": "做饭需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_shopping": {"zh": "购物需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_going_out": {"zh": "外出需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    "care_needs_medication": {"zh": "用药需求", "hint": "不需要/偶尔提醒/经常协助/离不开人"},
    
    # 健康问题
    "chronic_diseases": {"zh": "慢性病", "hint": "列举病名"},
    "recent_hospitalization": {"zh": "近期住院", "hint": "是/否，原因"},
    "falls_last_3months": {"zh": "近3月跌倒", "hint": "是/否，次数"},
    "incontinence": {"zh": "大小便失禁", "hint": "无/偶尔/经常"},
    "weight_loss": {"zh": "体重下降", "hint": "无/轻微/明显"},
    "swallowing_difficulty": {"zh": "吞咽困难", "hint": "无/偶尔/经常"},
    "mental_state_decline": {"zh": "精神状态下降", "hint": "无/轻微/明显"},
    "cognitive_issues": {"zh": "认知问题", "hint": "无/偶尔/经常"},
    
    # 家属观察
    "sleep_observation": {"zh": "睡眠观察", "hint": "好/一般/差"},
    "going_out_safety": {"zh": "外出安全", "hint": "放心/有点担心/很担心"},
    "emotional_state": {"zh": "情绪状态", "hint": "好/一般/低落/焦虑"},
    "memory_decline": {"zh": "记忆力下降", "hint": "无/轻微/明显"},
    
    # 用药
    "medications": {"zh": "目前用药", "hint": "列举药名"},
    "medication_compliance": {"zh": "用药依从性", "hint": "规律/偶尔忘/经常忘"},
    
    # 照护困难
    "care_challenges": {"zh": "照护困难", "hint": "自由描述"},
    "care_needs": {"zh": "需要的帮助", "hint": "自由描述"}
}
