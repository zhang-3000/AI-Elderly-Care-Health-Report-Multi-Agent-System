"""
AI 养老健康多 Agent 协作系统 V2.0
新版：健康评估与照护行动计划

改进：
1. 新增 ActionPlanAgent - 生成可执行的行动计划
2. 新增 PriorityAgent - 智能排序优先级
3. 调整 RiskAgent - 短期/中期风险预测
4. 调整 FactorAgent - 健康画像生成
5. 重构 ReportAgent - 新版报告模板
"""

import json
import logging
import pandas as pd
import os
from openai import OpenAI
import time
from typing import Callable, Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

try:
    from rag import PageIndexRAGAgent
except Exception:
    PageIndexRAGAgent = None

# DeepSeek API 配置 - 优先使用环境变量
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 临时清除代理环境变量（因为 Cursor 的代理返回 403）
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    if key in os.environ:
        del os.environ[key]

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


LLM_TIMEOUT_SECONDS = max(_env_float("DEEPSEEK_TIMEOUT_SECONDS", 180.0), 1.0)
LLM_MAX_RETRIES = max(_env_int("DEEPSEEK_MAX_RETRIES", 2), 0)
LLM_RETRY_DELAY_SECONDS = max(_env_float("DEEPSEEK_RETRY_DELAY_SECONDS", 2.0), 0.0)


RAG_ENABLED = _env_flag("RAG_ENABLED", False)
DEFAULT_RAG_INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "rag_indexes" / "default_index.json"
RAG_INDEX_PATH = Path(os.getenv("RAG_INDEX_PATH", str(DEFAULT_RAG_INDEX_PATH))).expanduser()
RAG_TOP_K = max(int(os.getenv("RAG_TOP_K", "3")), 1)


@dataclass
class UserProfile:
    """用户画像数据结构"""
    # 人口学
    age: int = None
    sex: str = None
    province: str = None
    residence: str = None
    education_years: int = None
    marital_status: str = None
    
    # 健康限制（重要指标）
    health_limitation: str = None  # e0: 过去6个月是否因健康问题限制了活动
    
    # BADL (6项)
    badl_bathing: str = None
    badl_dressing: str = None
    badl_toileting: str = None
    badl_transferring: str = None
    badl_continence: str = None
    badl_eating: str = None
    
    # IADL (8项)
    iadl_visiting: str = None
    iadl_shopping: str = None
    iadl_cooking: str = None
    iadl_laundry: str = None
    iadl_walking: str = None
    iadl_carrying: str = None
    iadl_crouching: str = None
    iadl_transport: str = None
    
    # 慢性病
    hypertension: str = None
    diabetes: str = None
    heart_disease: str = None
    stroke: str = None
    cataract: str = None
    cancer: str = None
    arthritis: str = None
    
    # 认知功能
    cognition_time: str = None
    cognition_month: str = None
    cognition_season: str = None
    cognition_place: str = None
    cognition_calc: List[str] = None
    cognition_draw: str = None
    
    # 心理状态
    depression: str = None
    anxiety: str = None
    loneliness: str = None
    
    # 生活方式
    smoking: str = None
    drinking: str = None
    exercise: str = None
    sleep_quality: str = None
    
    # 生理指标
    weight: float = None
    height: float = None
    vision: str = None
    hearing: str = None
    
    # 社会支持
    living_arrangement: str = None
    cohabitants: int = None
    financial_status: str = None
    income: float = None
    medical_insurance: str = None
    caregiver: str = None
    
    user_type: str = "elderly"


# ============ Agent 基类 ============
class BaseAgent:
    """Agent 基类（增强版 - 支持 RAG）"""
    
    def __init__(self, name: str, system_prompt: str, knowledge_agent=None):
        self.name = name
        self.system_prompt = system_prompt
        self.knowledge_agent = knowledge_agent  # 可选的知识检索能力

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        error_name = error.__class__.__name__.lower()
        error_text = str(error).lower()
        retry_markers = (
            "connection",
            "timeout",
            "timed out",
            "rate limit",
            "temporarily unavailable",
            "server disconnected",
            "502",
            "503",
            "504",
        )
        return (
            "connection" in error_name
            or "timeout" in error_name
            or any(marker in error_text for marker in retry_markers)
        )
    
    def call_llm(self, user_prompt: str, temperature: float = 0.3) -> str:
        """调用 LLM"""
        total_attempts = LLM_MAX_RETRIES + 1
        for attempt in range(1, total_attempts + 1):
            started_at = time.monotonic()
            try:
                logger.info(
                    "[%s] LLM request started attempt=%s/%s model=%s prompt_chars=%s timeout=%.1fs",
                    self.name,
                    attempt,
                    total_attempts,
                    DEEPSEEK_MODEL,
                    len(user_prompt),
                    LLM_TIMEOUT_SECONDS,
                )
                response = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=temperature,
                    max_tokens=2000,
                    timeout=LLM_TIMEOUT_SECONDS,
                )
                duration = time.monotonic() - started_at
                logger.info(
                    "[%s] LLM request finished attempt=%s/%s duration=%.2fs",
                    self.name,
                    attempt,
                    total_attempts,
                    duration,
                )
                return response.choices[0].message.content.strip()
            except Exception as error:
                duration = time.monotonic() - started_at
                retryable = attempt < total_attempts and self._is_retryable_error(error)
                logger.warning(
                    "[%s] LLM request failed attempt=%s/%s duration=%.2fs retryable=%s error=%s",
                    self.name,
                    attempt,
                    total_attempts,
                    duration,
                    retryable,
                    error,
                )
                if not retryable:
                    raise
                sleep_seconds = LLM_RETRY_DELAY_SECONDS * attempt
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
    
    def call_llm_with_rag(
        self, 
        user_prompt: str, 
        rag_query: Optional[str] = None,
        rag_top_k: int = 2,
        temperature: float = 0.3
    ) -> str:
        """
        调用 LLM，可选地使用 RAG 增强
        
        Args:
            user_prompt: 用户提示词
            rag_query: RAG 检索查询（如果为 None 则不使用 RAG）
            rag_top_k: RAG 返回结果数量
            temperature: LLM 温度参数
        
        Returns:
            LLM 响应文本
        """
        # 如果提供了 RAG 查询且知识代理可用
        if rag_query and self.knowledge_agent:
            try:
                rag_result = self.knowledge_agent.retrieve(rag_query, top_k=rag_top_k)
                knowledge_context = rag_result.get('context', '')
                
                if knowledge_context:
                    # 将知识注入到 prompt 中
                    enhanced_prompt = f"""{user_prompt}

【专业知识参考】
{knowledge_context}

请结合以上专业标准和指南进行分析，确保建议的科学性和权威性。"""
                    user_prompt = enhanced_prompt
            except Exception as e:
                print(f"⚠️ {self.name} RAG 检索失败: {e}")
        
        # 调用 LLM
        return self.call_llm(user_prompt, temperature)

    
    def parse_json(self, text: str) -> Dict:
        """解析 JSON 输出"""
        try:
            if '```' in text:
                text = text.split('```')[1]
                if text.startswith('json'):
                    text = text[4:]
            return json.loads(text.strip())
        except:
            return {"error": "JSON解析失败", "raw": text}


# ============ Stage 1: 状态判定 Agent ============
class StatusAgent(BaseAgent):
    """状态判定 Agent - 基于 BADL/IADL 判定失能状态"""
    
    def __init__(self):
        system_prompt = """你是失能状态判定专家。根据用户的健康限制、BADL 和 IADL 数据判定失能状态。

【重要参考指标】
健康限制（e0）："过去6个月是否因健康问题限制了活动"
- "否" = 无明显健康限制
- "是，有些限制" = 轻度限制
- "是，严重限制" = 重度限制
这是一个综合健康状态的前置指标，需要结合BADL/IADL综合判断。

【判定标准】
BADL（基本日常生活活动）6项：洗澡、穿衣、上厕所、室内活动、大小便控制、吃饭
- "不需要帮助" = 完全自理
- "部分帮助" = 部分依赖
- "多方面帮助" = 完全依赖

IADL（工具性日常生活活动）8项：串门、购物、做饭、洗衣、步行1km、提重物、蹲起、公共交通
- "能" = 完全自理
- "有点困难" = 部分困难
- "不能做" = 完全不能

【状态分类】
- 状态 0（功能完好）：所有 BADL 完全自理 且 IADL 大部分能做
- 状态 1（需要部分协助）：1-3个 BADL 完全依赖，或 1-7个 IADL 完全不能
- 状态 2（需要全面照护）：≥4个 BADL 完全依赖，或 8个 IADL 全部不能

【综合判断原则】
- 健康限制"严重"且BADL多项依赖 → 倾向状态2
- 健康限制"有些"且IADL部分困难 → 倾向状态1
- 健康限制"否"且功能良好 → 倾向状态0

【输出格式】JSON
{
    "status": 0或1或2,
    "status_name": "功能完好/需要部分协助/需要全面照护",
    "status_description": "生活自理/需要较多协助/需要全面照护支持",
    "health_limitation_impact": "健康限制对功能的影响说明",
    "badl_depend_count": BADL完全依赖数量,
    "iadl_unable_count": IADL完全不能数量,
    "badl_details": ["具体受限的BADL项目"],
    "iadl_details": ["具体受限的IADL项目"],
    "explanation": "用通俗语言解释判定依据（80字以内）"
}"""
        super().__init__("StatusAgent", system_prompt)
    
    def judge(self, profile: UserProfile) -> Dict:
        """判定失能状态"""
        badl_info = f"""
【健康限制】
过去6个月是否因健康问题限制了活动: {profile.health_limitation}

【BADL数据（基本日常生活活动能力）】
- 洗澡: {profile.badl_bathing}
- 穿衣: {profile.badl_dressing}
- 上厕所: {profile.badl_toileting}
- 室内活动: {profile.badl_transferring}
- 大小便控制: {profile.badl_continence}
- 吃饭: {profile.badl_eating}

【IADL数据（工具性日常生活活动能力）】
- 串门: {profile.iadl_visiting}
- 购物: {profile.iadl_shopping}
- 做饭: {profile.iadl_cooking}
- 洗衣: {profile.iadl_laundry}
- 步行1km: {profile.iadl_walking}
- 提重物: {profile.iadl_carrying}
- 蹲起: {profile.iadl_crouching}
- 公共交通: {profile.iadl_transport}
"""
        result = self.call_llm(f"请判定以下老人的失能状态：\n{badl_info}")
        return self.parse_json(result)



# ============ Stage 2: 风险预测 Agent (V2 - 调整) ============
class RiskAgentV2(BaseAgent):
    """风险预测 Agent V2 - 按时间维度预测短期/中期风险"""
    
    def __init__(self):
        system_prompt = """你是老年健康风险预测专家。请按时间维度评估风险，便于家属制定行动计划。

【风险识别原则】
1. 优先识别"最要命、最可防"的风险
2. 给出具体触发场景，不要抽象描述
3. 区分可预防 vs 不可预防
4. 用因果链表达中期风险（如：营养↓→肌肉↓→跌倒↑）

【高风险因素（CLHLS证据）】
- 年龄≥85岁、中风病史、认知障碍、多病共存、独居无照护
- 高血压/糖尿病/心脏病、抑郁、缺乏锻炼、农村低教育

【输出格式】JSON
{
    "short_term_risks": [
        {
            "timeframe": "1-4周",
            "risk": "具体风险名称",
            "trigger": "触发场景（如夜间起床、卫生间）",
            "severity": "高/中/低",
            "preventable": true/false,
            "prevention_key": "预防关键点"
        }
    ],
    "medium_term_risks": [
        {
            "timeframe": "1-6月",
            "risk": "具体风险名称",
            "chain": "因果链（如：营养↓→肌肉↓→跌倒↑）",
            "severity": "高/中/低",
            "preventable": true/false,
            "prevention_key": "预防关键点"
        }
    ],
    "overall_risk_level": "低/中/高",
    "risk_summary": "风险总结（80字以内）"
}"""
        super().__init__("RiskAgentV2", system_prompt)
    
    def predict(self, profile: UserProfile, current_status: int) -> Dict:
        """预测风险（增强版 - 支持 RAG）"""
        risk_factors = f"""
【基本信息】
当前失能状态: {current_status} (0=无失能, 1=部分失能, 2=严重失能)
年龄: {profile.age}岁
性别: {profile.sex}
居住地: {profile.residence}
教育年限: {profile.education_years}年

【健康限制】
过去6个月活动限制: {profile.health_limitation}

【慢性病情况】
- 高血压: {profile.hypertension}
- 糖尿病: {profile.diabetes}
- 心脏病: {profile.heart_disease}
- 中风: {profile.stroke}
- 白内障: {profile.cataract}
- 癌症: {profile.cancer}
- 关节炎: {profile.arthritis}

【认知功能】
- 时间定向: {profile.cognition_time}
- 月份定向: {profile.cognition_month}
- 季节定向: {profile.cognition_season}
- 地点定向: {profile.cognition_place}
- 计算能力: {profile.cognition_calc}
- 画图能力: {profile.cognition_draw}

【心理状态】
- 抑郁感: {profile.depression}
- 焦虑感: {profile.anxiety}
- 孤独感: {profile.loneliness}

【生活方式】
- 吸烟: {profile.smoking}
- 饮酒: {profile.drinking}
- 锻炼: {profile.exercise}
- 睡眠质量: {profile.sleep_quality}

【生理指标】
- 体重: {profile.weight}kg
- 身高: {profile.height}cm
- 视力: {profile.vision}
- 听力: {profile.hearing}

【社会支持】
- 居住安排: {profile.living_arrangement}
- 同住人数: {profile.cohabitants}人
- 经济状况: {profile.financial_status}
- 照护者: {profile.caregiver}
- 医保: {profile.medical_insurance}
"""
        
        # 构建 RAG 查询：针对高风险因素
        rag_query = None
        if self.knowledge_agent:
            # 识别关键风险因素
            risk_keywords = []
            age = int(profile.age) if profile.age else 0
            if age >= 85:
                risk_keywords.append("高龄老人")
            if profile.stroke in ["是", "有"]:
                risk_keywords.append("中风")
            if profile.heart_disease in ["是", "有"]:
                risk_keywords.append("心脏病")
            if profile.diabetes in ["是", "有"]:
                risk_keywords.append("糖尿病")
            if current_status >= 1:
                risk_keywords.append("失能")
            
            if risk_keywords:
                rag_query = f"{age}岁 {' '.join(risk_keywords[:3])} 风险预防 健康标准"
        
        # 使用 RAG 增强的 LLM 调用
        result = self.call_llm_with_rag(
            f"请评估以下老人的短期和中期风险：\n{risk_factors}",
            rag_query=rag_query,
            rag_top_k=2
        )
        return self.parse_json(result)



# ============ Stage 3: 因素分析 Agent (V2 - 调整) ============
class FactorAgentV2(BaseAgent):
    """因素分析 Agent V2 - 生成健康画像"""
    
    def __init__(self):
        system_prompt = """你是老年健康因素分析专家。生成"健康画像"，清晰呈现"好在哪里、短板在哪里"。

【健康画像结构】
1. 功能状态：当前失能状态的通俗描述
2. 优势（需要继续保持）：列出保护因素
3. 主要问题（本报告重点要解决的）：列出核心风险因素

【CLHLS 研究证实的因素】
不可改变：年龄、性别、既往中风、教育程度
可改变：体力活动、慢性病管理、认知训练、社会参与、营养状态、情绪管理

【输出格式】JSON
{
    "functional_status": {
        "level": "功能完好/需要部分协助/需要全面照护",
        "description": "可独立完成穿衣、进食、洗漱、如厕，并能处理家务、购物等事务"
    },
    "strengths": [
        "有活动习惯",
        "头脑清晰",
        "未提示高血压/糖尿病等部分慢病负担"
    ],
    "main_problems": [
        {
            "problem": "心脏病",
            "impact": "需要明确类型与严重程度，避免急性加重",
            "priority": 1
        },
        {
            "problem": "情绪困扰/孤独",
            "impact": "会降低活动与进食意愿，间接加速功能下降",
            "priority": 2
        }
    ],
    "unchangeable_factors": ["年龄", "性别", "教育程度"],
    "changeable_factors": ["体力活动", "慢性病管理", "情绪管理"]
}"""
        super().__init__("FactorAgentV2", system_prompt)
    
    def analyze(self, profile: UserProfile, status_result: Dict, risk_result: Dict) -> Dict:
        """分析影响因素并生成健康画像"""
        
        # 计算BMI（如果有身高体重）
        bmi_info = ""
        if profile.weight and profile.height:
            try:
                weight = float(profile.weight)
                height = float(profile.height) / 100  # 转换为米
                bmi = weight / (height ** 2)
                bmi_info = f"BMI: {bmi:.1f} "
                if bmi < 18.5:
                    bmi_info += "(偏瘦，营养不足风险)"
                elif bmi < 24:
                    bmi_info += "(正常)"
                elif bmi < 28:
                    bmi_info += "(超重)"
                else:
                    bmi_info += "(肥胖)"
            except:
                pass
        
        all_info = f"""
【基本信息】
年龄: {profile.age}岁, 性别: {profile.sex}
居住地: {profile.residence}, 教育: {profile.education_years}年
婚姻: {profile.marital_status}

【当前状态】
失能状态: {status_result.get('status_name', '未知')}
判定依据: {status_result.get('explanation', '')}
健康限制: {profile.health_limitation}
BADL受限: {status_result.get('badl_details', [])}
IADL受限: {status_result.get('iadl_details', [])}

【慢性病负担】
- 高血压: {profile.hypertension}
- 糖尿病: {profile.diabetes}
- 心脏病: {profile.heart_disease}
- 中风: {profile.stroke}
- 白内障: {profile.cataract}
- 癌症: {profile.cancer}
- 关节炎: {profile.arthritis}

【认知功能】
- 时间定向: {profile.cognition_time}
- 月份定向: {profile.cognition_month}
- 季节定向: {profile.cognition_season}
- 地点定向: {profile.cognition_place}
- 计算能力: {profile.cognition_calc}
- 画图能力: {profile.cognition_draw}

【心理状态】
- 抑郁感: {profile.depression}
- 焦虑感: {profile.anxiety}
- 孤独感: {profile.loneliness}

【生活方式】
- 吸烟: {profile.smoking}
- 饮酒: {profile.drinking}
- 锻炼: {profile.exercise}
- 睡眠质量: {profile.sleep_quality}

【生理指标】
- 体重: {profile.weight}kg, 身高: {profile.height}cm
- {bmi_info}
- 视力: {profile.vision}
- 听力: {profile.hearing}

【社会支持】
- 居住: {profile.living_arrangement}
- 同住: {profile.cohabitants}人
- 照护者: {profile.caregiver}
- 医保: {profile.medical_insurance}
- 经济: {profile.financial_status}

【风险评估结果】
- 总体风险: {risk_result.get('overall_risk_level', '未知')}
- 短期风险: {[r.get('risk') for r in risk_result.get('short_term_risks', [])]}
- 中期风险: {[r.get('risk') for r in risk_result.get('medium_term_risks', [])]}
"""
        result = self.call_llm(f"请生成健康画像：\n{all_info}")
        return self.parse_json(result)



# ============ Stage 4: 行动计划 Agent (新增) ============
class ActionPlanAgent(BaseAgent):
    """行动计划生成 Agent - 将建议转化为可执行的行动计划"""
    
    def __init__(self):
        system_prompt = """你是照护行动计划专家。将健康建议转化为可执行的行动计划。

【行动计划要素】
- 负责人：明确谁来做（家属/照护者/老人自己）
- 怎么做：具体步骤，可直接执行
- 完成标准：可验证的结果
- 时间框架：建议完成时间
- 难度/成本/影响：便于优先级排序

【输出原则】
1. 每个行动都要"可落地、可验证、可完成"
2. 避免"建议锻炼"这种模糊表述，要具体到"每天上下午各5-10分钟"
3. 给出"完成标准"而非"做法标准"
4. 考虑农村/城市、独居/同住等实际情况

【输出格式】JSON
{
    "actions": [
        {
            "action_id": "A1",
            "title": "把"就医通道"先建立起来",
            "subtitle": "医保 + 就近固定就诊点",
            "responsible": "家属/主要照护者",
            "how_to_do": [
                "带身份证、户口本去村委会或乡镇医保经办点办理城乡居民医保",
                "若医保短期办不下来：先去乡镇卫生院建档"
            ],
            "completion_criteria": "明确"以后看病去哪里、谁负责陪诊、紧急时打谁电话"",
            "timeframe": "1-2周内完成",
            "difficulty": "中",
            "cost": "低",
            "impact": "高",
            "category": "医疗保障"
        }
    ]
}"""
        super().__init__("ActionPlanAgent", system_prompt)
    
    def generate(self, profile: UserProfile, status_result: Dict,
                 risk_result: Dict, factor_result: Dict,
                 knowledge_context: str = "") -> Dict:
        """生成行动计划（增强版 - 支持 RAG）"""
        context = f"""
【用户画像】
年龄: {profile.age}岁, 性别: {profile.sex}
当前状态: {status_result.get('status_name', '未知')}
总体风险: {risk_result.get('overall_risk_level', '未知')}

【短期风险（1-4周）】
{json.dumps(risk_result.get('short_term_risks', []), ensure_ascii=False, indent=2)}

【中期风险（1-6月）】
{json.dumps(risk_result.get('medium_term_risks', []), ensure_ascii=False, indent=2)}

【主要问题】
{json.dumps(factor_result.get('main_problems', []), ensure_ascii=False, indent=2)}

【可改变因素】
{factor_result.get('changeable_factors', [])}

【当前生活方式】
锻炼: {profile.exercise}
吸烟: {profile.smoking}
饮酒: {profile.drinking}
睡眠: {profile.sleep_quality}

【社会支持】
居住: {profile.living_arrangement}
照护者: {profile.caregiver}
医保: {profile.medical_insurance}
居住地: {profile.residence}

【用户类型】
{profile.user_type}（请根据用户类型调整语言风格）

请生成8-12个具体的行动计划，涵盖：
1. 医疗保障与就医通道
2. 慢性病管理
3. 居家安全（防跌倒）
4. 活动与营养
5. 情绪与社交
6. 照护资源对接
"""
        
        # 构建 RAG 查询：针对最紧迫的行动需求
        rag_query = None
        if self.knowledge_agent:
            # 提取高风险项
            high_risks = [r for r in risk_result.get('short_term_risks', []) 
                         if r.get('severity') == '高']
            
            age = int(profile.age) if profile.age else 0
            
            if high_risks:
                # 针对高风险生成查询
                risk_name = high_risks[0].get('risk', '')
                rag_query = f"{age}岁老人 {risk_name} 预防措施 具体方法"
            else:
                # 针对慢性病管理生成查询
                diseases = []
                if profile.hypertension in ["是", "有"]:
                    diseases.append("高血压")
                if profile.diabetes in ["是", "有"]:
                    diseases.append("糖尿病")
                if diseases:
                    rag_query = f"老年人 {' '.join(diseases[:2])} 日常管理 居家照护"
        
        if knowledge_context:
            context += f"""

【RAG知识库参考】
以下内容来自项目内置知识库，仅作辅助参考。请结合当前老人的具体情况吸收使用，不要逐字照抄，也不要生成与个体情况矛盾的建议。

{knowledge_context}
"""
        
        # 使用 RAG 增强的 LLM 调用
        result = self.call_llm_with_rag(
            context,
            rag_query=rag_query,
            rag_top_k=2
        )
        return self.parse_json(result)



# ============ Stage 5: 优先级排序 Agent (新增) ============
class PriorityAgent(BaseAgent):
    """优先级排序 Agent - 智能排序行动计划"""
    
    def __init__(self):
        system_prompt = """你是行动优先级排序专家。基于风险紧急度、可行性、成本效益排序。

【排序维度】
1. 风险严重度（会不会要命）- 权重40%
2. 可预防性（做了有没有用）- 权重30%
3. 可行性（能不能做到）- 权重20%
4. 成本效益（投入产出比）- 权重10%

【分级原则】
- A级（第一优先）：3项以内，"最要命、最可防、最好做"
- B级（第二优先）：4-7项，日常维持类
- C级（第三优先）：长期改善类

【输出格式】JSON
{
    "priority_a": [
        {
            "action_id": "A3",
            "rank": 1,
            "reason": "成本低但收益最大，立即可做",
            "urgency": "立刻"
        }
    ],
    "priority_b": [...],
    "priority_c": [...],
    "排序说明": "简要说明排序逻辑"
}"""
        super().__init__("PriorityAgent", system_prompt)
    
    def rank(self, actions: List[Dict], risks: Dict) -> Dict:
        """排序行动计划"""
        context = f"""
【所有行动计划】
{json.dumps(actions, ensure_ascii=False, indent=2)}

【风险评估】
短期风险: {json.dumps(risks.get('short_term_risks', []), ensure_ascii=False)}
中期风险: {json.dumps(risks.get('medium_term_risks', []), ensure_ascii=False)}
总体风险: {risks.get('overall_risk_level', '未知')}

请将行动计划按优先级分为A/B/C三级，每级给出排序理由。
"""
        result = self.call_llm(context)
        return self.parse_json(result)



# ============ Stage 6: 反思校验 Agent ============
class ReviewAgent(BaseAgent):
    """反思校验 Agent - 检查输出质量和一致性"""
    
    def __init__(self):
        system_prompt = """你是健康报告质量审核专家，负责检查报告的准确性、一致性和安全性。

【检查要点】
1. 一致性：状态判定与风险预测是否矛盾
2. 合理性：行动计划是否与风险因素匹配
3. 安全性：是否有需要紧急就医的情况
4. 可执行性：行动计划是否具体可落地
5. 完整性：是否覆盖了所有必要内容

【紧急情况识别】
需要立即就医的情况：
- 突发胸痛、呼吸困难
- 新发偏瘫或言语不清
- 急性意识改变
- 严重抑郁、自伤想法
- 快速功能下降（几周内）

【输出格式】JSON
{
    "consistency_check": {"passed": true/false, "issues": []},
    "safety_check": {"urgent": true/false, "urgent_reason": ""},
    "executability_check": {"passed": true/false, "issues": []},
    "completeness_check": {"passed": true/false, "missing": []},
    "suggestions": ["改进建议"],
    "overall_quality": "优/良/中/差",
    "approved": true/false
}"""
        super().__init__("ReviewAgent", system_prompt)
    
    def review(self, status_result: Dict, risk_result: Dict,
               factor_result: Dict, action_result: Dict, priority_result: Dict) -> Dict:
        """审核报告"""
        report_content = f"""
【状态判定结果】
{json.dumps(status_result, ensure_ascii=False, indent=2)}

【风险预测结果】
{json.dumps(risk_result, ensure_ascii=False, indent=2)}

【因素分析结果】
{json.dumps(factor_result, ensure_ascii=False, indent=2)}

【行动计划结果】
{json.dumps(action_result, ensure_ascii=False, indent=2)}

【优先级排序结果】
{json.dumps(priority_result, ensure_ascii=False, indent=2)}
"""
        result = self.call_llm(f"请审核以下健康评估报告：\n{report_content}")
        return self.parse_json(result)



# ============ Stage 7: 报告生成 Agent (V2 - 重构) ============
class ReportAgentV2(BaseAgent):
    """报告生成 Agent V2 - 使用新版报告模板"""
    
    def __init__(self):
        system_prompt = """你是健康报告撰写专家，负责将评估结果整合为"健康评估与照护行动计划"。

【报告结构】
0. 报告说明
1. 健康报告总结（3句话）
2. 您的健康画像
3. 风险因素（按时间优先级）
4. 健康建议（按优先级分级）
5. 温馨寄语

【语言风格】
- 口语化、接地气
- 避免医学术语
- 强调"可以做什么"
- 每个建议都有负责人、怎么做、完成标准

【输出格式】
直接输出 Markdown 格式的报告，不要 JSON。"""
        super().__init__("ReportAgentV2", system_prompt)
    
    def generate_report(self, profile: UserProfile, status_result: Dict,
                        risk_result: Dict, factor_result: Dict,
                        action_result: Dict, priority_result: Dict,
                        review_result: Dict, knowledge_context: str = "") -> str:
        """生成最终报告"""
        
        # 检查是否有紧急情况
        urgent = review_result.get('safety_check', {}).get('urgent', False)
        urgent_reason = review_result.get('safety_check', {}).get('urgent_reason', '')
        
        context = f"""
请根据以下评估结果，生成一份完整的"健康评估与照护行动计划"。

【用户信息】
年龄: {profile.age}岁, 性别: {profile.sex}
用户类型: {profile.user_type}（请据此调整语言风格）

【状态判定】
当前状态: {status_result.get('status_name', '未知')}
状态描述: {status_result.get('status_description', '')}
判定依据: {status_result.get('explanation', '')}

【健康画像】
功能状态: {factor_result.get('functional_status', {})}
优势: {factor_result.get('strengths', [])}
主要问题: {factor_result.get('main_problems', [])}

【风险评估】
总体风险: {risk_result.get('overall_risk_level', '未知')}
短期风险(1-4周): {risk_result.get('short_term_risks', [])}
中期风险(1-6月): {risk_result.get('medium_term_risks', [])}

【行动计划（已按优先级排序）】
A级（第一优先）: {priority_result.get('priority_a', [])}
B级（第二优先）: {priority_result.get('priority_b', [])}
C级（第三优先）: {priority_result.get('priority_c', [])}

【审核结果】
是否紧急: {urgent}
紧急原因: {urgent_reason}

请按以下结构生成报告：

# 健康评估与照护行动计划

## 0. 报告说明
本报告基于您/家属提供的信息进行风险提示与照护建议，不能替代医生面诊；涉及用药与检查，请以医生意见为准。

## 1. 健康报告总结
用3句话总结：
1）整体身体功能评估
2）目前的核心问题
3）干预建议概要

## 2. 您的健康画像（现在"好在哪里、短板在哪里"）
### （1）功能状态：[状态名称]
[状态描述]

### （2）优势（需要继续保持）
[列出优势]

### （3）主要问题（本报告重点要解决的）
[列出主要问题及影响]

## 3. 风险因素（按时间或优先级最高的事来写，方便落地）
### 近期（1-4周）重点风险：
[列出短期风险及触发场景]

### 中期（1-6月）重点风险：
[列出中期风险及因果链]

**注：** 风险提示不代表一定会发生，而是提醒优先把"最要命、最可防"的环节补上。

## 4. 健康建议

### A. 第一优先级
[对每个行动，按以下格式输出]
**1）[行动标题]**
- 负责人：[谁来做]
- 怎么做：[具体步骤]
- 完成标准：[如何验证]

### B. 第二优先级
[同上格式]

### C. 第三优先级
[同上格式]

## 5. 温馨寄语
[一段温暖、鼓励的话，强调"按计划慢慢来，能做多少就做多少"]
"""
        if knowledge_context:
            context += f"""

【RAG知识库参考】
以下材料来自项目知识库，请仅在与当前老人情况一致时吸收为通俗建议，不要照抄原文，也不要引用过于学术化的表述：

{knowledge_context}
"""
        return self.call_llm(context, temperature=0.5)



# ============ 调度中心 Orchestrator V2 ============
class OrchestratorAgentV2:
    """调度中心 V2 - 协调7个Agent的执行（增强版 - RAG 深度融合）"""
    
    def __init__(self):
        # 首先初始化知识代理
        self.knowledge_agent = self._init_knowledge_agent()
        
        # 初始化各个 Agent，并传递知识代理
        self.status_agent = StatusAgent()
        self.status_agent.knowledge_agent = self.knowledge_agent
        
        self.risk_agent = RiskAgentV2()
        self.risk_agent.knowledge_agent = self.knowledge_agent
        
        self.factor_agent = FactorAgentV2()
        self.factor_agent.knowledge_agent = self.knowledge_agent
        
        self.action_agent = ActionPlanAgent()
        self.action_agent.knowledge_agent = self.knowledge_agent
        
        self.priority_agent = PriorityAgent()
        self.priority_agent.knowledge_agent = self.knowledge_agent
        
        self.review_agent = ReviewAgent()
        self.review_agent.knowledge_agent = self.knowledge_agent
        
        self.report_agent = ReportAgentV2()
        self.report_agent.knowledge_agent = self.knowledge_agent

    def _init_knowledge_agent(self):
        """初始化知识代理（使用新的 KnowledgeAgent）"""
        if not RAG_ENABLED or PageIndexRAGAgent is None:
            return None
        if not RAG_INDEX_PATH.exists():
            print(f"⚠️ RAG 已启用，但索引文件不存在: {RAG_INDEX_PATH}")
            return None
        try:
            from knowledge_agent import KnowledgeAgent
            rag_agent = PageIndexRAGAgent(index_path=str(RAG_INDEX_PATH))
            return KnowledgeAgent(rag_agent)
        except Exception as error:
            print(f"⚠️ Knowledge Agent 初始化失败: {error}")
            return None

    @staticmethod
    def _emit_stage_event(
        stage_callback: Optional[Callable[[Dict[str, Any]], None]],
        agent: str,
        status: str,
        message: str,
        duration_seconds: Optional[float] = None,
    ) -> None:
        if stage_callback is None:
            return
        payload: Dict[str, Any] = {
            "agent": agent,
            "status": status,
            "message": message,
        }
        if duration_seconds is not None:
            payload["duration_seconds"] = round(duration_seconds, 2)
        stage_callback(payload)

    def _run_stage(
        self,
        stage_key: str,
        running_message: str,
        completed_message: str,
        func: Callable[[], Any],
        stage_callback: Optional[Callable[[Dict[str, Any]], None]],
    ) -> Any:
        self._emit_stage_event(stage_callback, stage_key, "running", running_message)
        started_at = time.monotonic()
        try:
            result = func()
        except Exception as error:
            duration = time.monotonic() - started_at
            logger.exception("Orchestrator stage failed: %s", stage_key)
            self._emit_stage_event(
                stage_callback,
                stage_key,
                "failed",
                f"{running_message}失败: {error}",
                duration_seconds=duration,
            )
            raise

        duration = time.monotonic() - started_at
        self._emit_stage_event(
            stage_callback,
            stage_key,
            "completed",
            completed_message,
            duration_seconds=duration,
        )
        return result
    
    def run(
        self,
        profile: UserProfile,
        verbose: bool = True,
        stage_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict:
        """执行完整评估流程"""
        results = {}
        knowledge_context = ""
        
        # Stage 1: 状态判定
        if verbose:
            print("🔍 Stage 1: 状态判定 Agent 执行中...")
        results['status'] = self._run_stage(
            "status",
            "正在判定功能状态...",
            "功能状态判定完成",
            lambda: self.status_agent.judge(profile),
            stage_callback,
        )
        if verbose:
            print(f"   → 状态: {results['status'].get('status_name', '未知')}")
        
        # Stage 2: 风险预测（V2）
        if verbose:
            print("📈 Stage 2: 风险预测 Agent V2 执行中...")
        current_status = results['status'].get('status', 0)
        results['risk'] = self._run_stage(
            "risk",
            "正在进行风险预测...",
            "风险预测完成",
            lambda: self.risk_agent.predict(profile, current_status),
            stage_callback,
        )
        if verbose:
            print(f"   → 风险等级: {results['risk'].get('overall_risk_level', '未知')}")
            print(f"   → 短期风险数: {len(results['risk'].get('short_term_risks', []))}")
        
        # Stage 3: 因素分析（V2）
        if verbose:
            print("🔬 Stage 3: 因素分析 Agent V2 执行中...")
        results['factors'] = self._run_stage(
            "factors",
            "正在提取关键影响因素...",
            "关键影响因素分析完成",
            lambda: self.factor_agent.analyze(profile, results['status'], results['risk']),
            stage_callback,
        )
        if verbose:
            print(f"   → 主要问题数: {len(results['factors'].get('main_problems', []))}")

        # Stage 3.5: RAG 知识检索（可选 - 使用新的 KnowledgeAgent）
        if self.knowledge_agent is not None:
            if verbose:
                print("📚 Stage 3.5: 知识检索 Agent 执行中...")
            results['knowledge'] = self._run_stage(
                "knowledge",
                "正在检索知识库参考...",
                "知识库参考检索完成",
                lambda: self.knowledge_agent.retrieve_comprehensive(
                    profile,
                    results['status'],
                    results['risk'],
                    results['factors'],
                    top_k=RAG_TOP_K,
                ),
                stage_callback,
            )
            knowledge_context = results['knowledge'].get('combined_context', '')
            if verbose:
                total_hits = results['knowledge'].get('total_hits', 0)
                print(f"   → 总命中文档片段数: {total_hits}")
                risk_hits = len(results['knowledge'].get('risk_prevention', {}).get('hits', []))
                disease_hits = len(results['knowledge'].get('disease_management', {}).get('hits', []))
                training_hits = len(results['knowledge'].get('functional_training', {}).get('hits', []))
                if risk_hits > 0:
                    print(f"   → 风险预防知识: {risk_hits}条")
                if disease_hits > 0:
                    print(f"   → 疾病管理知识: {disease_hits}条")
                if training_hits > 0:
                    print(f"   → 功能训练知识: {training_hits}条")
        else:
            results['knowledge'] = {
                "enabled": False,
                "risk_prevention": {"hits": [], "context": ""},
                "disease_management": {"hits": [], "context": ""},
                "functional_training": {"hits": [], "context": ""},
                "combined_context": "",
                "total_hits": 0
            }
        
        # Stage 4: 行动计划生成（新增）
        if verbose:
            print("💡 Stage 4: 行动计划 Agent 执行中...")
        results['actions'] = self._run_stage(
            "actions",
            "正在生成干预行动建议...",
            "干预行动建议生成完成",
            lambda: self.action_agent.generate(
                profile,
                results['status'],
                results['risk'],
                results['factors'],
                knowledge_context=knowledge_context,
            ),
            stage_callback,
        )
        if verbose:
            print(f"   → 生成行动数: {len(results['actions'].get('actions', []))}")
        
        # Stage 5: 优先级排序（新增）
        if verbose:
            print("🎯 Stage 5: 优先级排序 Agent 执行中...")
        results['priority'] = self._run_stage(
            "priority",
            "正在排序建议优先级...",
            "建议优先级排序完成",
            lambda: self.priority_agent.rank(
                results['actions'].get('actions', []),
                results['risk'],
            ),
            stage_callback,
        )
        if verbose:
            print(f"   → A级优先: {len(results['priority'].get('priority_a', []))}项")
            print(f"   → B级优先: {len(results['priority'].get('priority_b', []))}项")
            print(f"   → C级优先: {len(results['priority'].get('priority_c', []))}项")
        
        # Stage 6: 反思校验
        if verbose:
            print("✅ Stage 6: 反思校验 Agent 执行中...")
        results['review'] = self._run_stage(
            "review",
            "正在进行结果复核...",
            "结果复核完成",
            lambda: self.review_agent.review(
                results['status'],
                results['risk'],
                results['factors'],
                results['actions'],
                results['priority'],
            ),
            stage_callback,
        )
        if verbose:
            quality = results['review'].get('overall_quality', '未知')
            print(f"   → 报告质量: {quality}")
        
        # Stage 7: 报告生成（V2）
        if verbose:
            print("📝 Stage 7: 报告生成 Agent V2 执行中...")
        results['report'] = self._run_stage(
            "report",
            "正在整理最终报告文本...",
            "最终报告文本整理完成",
            lambda: self.report_agent.generate_report(
                profile,
                results['status'],
                results['risk'],
                results['factors'],
                results['actions'],
                results['priority'],
                results['review'],
                knowledge_context=knowledge_context,
            ),
            stage_callback,
        )
        if verbose:
            print("   → 报告生成完成")
        
        return results


# ============ 数据加载与转换工具 ============
def load_user_profile_from_excel(excel_path: str, row_index: int = 0) -> UserProfile:
    """
    从Excel文件加载用户数据并转换为UserProfile
    
    参数:
        excel_path: Excel文件路径
        row_index: 数据行索引（注意：第0行是中文描述，实际数据从第1行开始）
    """
    print(f"正在加载数据: {excel_path}")
    df = pd.read_excel(excel_path)
    
    # 第一行是中文描述，实际数据从第二行开始
    df_data = df.iloc[1:].reset_index(drop=True)
    
    if row_index >= len(df_data):
        raise ValueError(f"行索引 {row_index} 超出数据范围（共{len(df_data)}行）")
    
    row = df_data.iloc[row_index]
    
    # 辅助函数：安全获取值
    def safe_get(col_name, default=None):
        if col_name in df_data.columns:
            val = row[col_name]
            if pd.isna(val):
                return default
            return str(val) if val is not None else default
        return default
    
    # 构建UserProfile（基于prepare_sample_data.py中的变量映射）
    profile = UserProfile(
        # 人口学
        age=safe_get('trueage'),
        sex=safe_get('a1'),
        province=safe_get('prov'),
        residence=safe_get('residenc'),
        education_years=safe_get('f1'),
        marital_status=safe_get('f41'),
        
        # 健康限制
        health_limitation=safe_get('e0'),
        
        # BADL (6项) - e1到e6
        badl_bathing=safe_get('e1'),
        badl_dressing=safe_get('e2'),
        badl_toileting=safe_get('e3'),
        badl_transferring=safe_get('e4'),
        badl_continence=safe_get('e5'),
        badl_eating=safe_get('e6'),
        
        # IADL (8项) - e7到e14
        iadl_visiting=safe_get('e7'),
        iadl_shopping=safe_get('e8'),
        iadl_cooking=safe_get('e9'),
        iadl_laundry=safe_get('e10'),
        iadl_walking=safe_get('e11'),
        iadl_carrying=safe_get('e12'),
        iadl_crouching=safe_get('e13'),
        iadl_transport=safe_get('e14'),
        
        # 慢性病
        hypertension=safe_get('g15a1'),
        diabetes=safe_get('g15b1'),
        heart_disease=safe_get('g15c1'),
        stroke=safe_get('g15d1'),
        cataract=safe_get('g15g1'),
        cancer=safe_get('g15i1'),
        arthritis=safe_get('g15n1'),
        
        # 认知功能
        cognition_time=safe_get('c11'),
        cognition_month=safe_get('c12'),
        cognition_season=safe_get('c14'),
        cognition_place=safe_get('c15'),
        cognition_calc=[safe_get('c31a', ''), safe_get('c31b', ''), safe_get('c31c', '')],
        cognition_draw=safe_get('c32'),
        
        # 心理状态
        depression=safe_get('b33'),
        anxiety=safe_get('b36'),
        loneliness=safe_get('b38'),
        
        # 生活方式
        smoking=safe_get('d71'),
        drinking=safe_get('d81'),
        exercise=safe_get('d91'),
        sleep_quality=safe_get('b310a'),
        
        # 生理指标
        weight=safe_get('g101'),
        height=safe_get('g1021'),
        vision=safe_get('g1'),
        hearing=safe_get('g106'),
        
        # 社会支持
        living_arrangement=safe_get('a51'),
        cohabitants=safe_get('a52'),
        financial_status=safe_get('f34'),
        income=safe_get('f35'),
        medical_insurance=f"城镇医保:{safe_get('f64e')}, 新农合:{safe_get('f64g')}",
        caregiver=safe_get('f5'),
        
        user_type="elderly"
    )
    
    return profile


def load_multiple_profiles(excel_path: str, n_samples: int = 50, random_state: int = 42) -> List[UserProfile]:
    """
    从Excel文件加载多个用户数据
    
    参数:
        excel_path: Excel文件路径
        n_samples: 抽样数量
        random_state: 随机种子
    
    返回:
        UserProfile列表
    """
    import numpy as np
    
    print(f"正在加载数据: {excel_path}")
    df = pd.read_excel(excel_path)
    
    # 第一行是中文描述，实际数据从第二行开始
    df_data = df.iloc[1:].reset_index(drop=True)
    
    print(f"数据加载完成，共 {len(df_data)} 条记录")
    
    # 随机抽样
    np.random.seed(random_state)
    if n_samples < len(df_data):
        sample_indices = np.random.choice(len(df_data), n_samples, replace=False)
        print(f"随机抽取 {n_samples} 条样本")
    else:
        sample_indices = range(len(df_data))
        print(f"使用全部 {len(df_data)} 条数据")
    
    # 加载所有样本
    profiles = []
    for idx in sample_indices:
        try:
            # 临时修改函数以支持已加载的DataFrame
            row = df_data.iloc[idx]
            
            def safe_get(col_name, default=None):
                if col_name in df_data.columns:
                    val = row[col_name]
                    if pd.isna(val):
                        return default
                    return str(val) if val is not None else default
                return default
            
            profile = UserProfile(
                age=safe_get('trueage'),
                sex=safe_get('a1'),
                province=safe_get('prov'),
                residence=safe_get('residenc'),
                education_years=safe_get('f1'),
                marital_status=safe_get('f41'),
                health_limitation=safe_get('e0'),
                badl_bathing=safe_get('e1'),
                badl_dressing=safe_get('e2'),
                badl_toileting=safe_get('e3'),
                badl_transferring=safe_get('e4'),
                badl_continence=safe_get('e5'),
                badl_eating=safe_get('e6'),
                iadl_visiting=safe_get('e7'),
                iadl_shopping=safe_get('e8'),
                iadl_cooking=safe_get('e9'),
                iadl_laundry=safe_get('e10'),
                iadl_walking=safe_get('e11'),
                iadl_carrying=safe_get('e12'),
                iadl_crouching=safe_get('e13'),
                iadl_transport=safe_get('e14'),
                hypertension=safe_get('g15a1'),
                diabetes=safe_get('g15b1'),
                heart_disease=safe_get('g15c1'),
                stroke=safe_get('g15d1'),
                cataract=safe_get('g15g1'),
                cancer=safe_get('g15i1'),
                arthritis=safe_get('g15n1'),
                cognition_time=safe_get('c11'),
                cognition_month=safe_get('c12'),
                cognition_season=safe_get('c14'),
                cognition_place=safe_get('c15'),
                cognition_calc=[safe_get('c31a', ''), safe_get('c31b', ''), safe_get('c31c', '')],
                cognition_draw=safe_get('c32'),
                depression=safe_get('b33'),
                anxiety=safe_get('b36'),
                loneliness=safe_get('b38'),
                smoking=safe_get('d71'),
                drinking=safe_get('d81'),
                exercise=safe_get('d91'),
                sleep_quality=safe_get('b310a'),
                weight=safe_get('g101'),
                height=safe_get('g1021'),
                vision=safe_get('g1'),
                hearing=safe_get('g106'),
                living_arrangement=safe_get('a51'),
                cohabitants=safe_get('a52'),
                financial_status=safe_get('f34'),
                income=safe_get('f35'),
                medical_insurance=f"城镇医保:{safe_get('f64e')}, 新农合:{safe_get('f64g')}",
                caregiver=safe_get('f5'),
                user_type="elderly"
            )
            profiles.append(profile)
        except Exception as e:
            print(f"  ⚠️  加载第 {idx} 行数据失败: {str(e)}")
            continue
    
    print(f"成功加载 {len(profiles)} 个用户画像")
    return profiles


def save_results(results: Dict, profile: UserProfile, output_dir: str = "./output", row_index: int = None):
    """
    保存评估结果
    
    参数:
        results: 评估结果
        profile: 用户画像
        output_dir: 输出目录
        row_index: 数据行索引（可选，用于文件名标识）
    """
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 构建文件名：包含年龄、性别、时间戳，可选行号
    if row_index is not None:
        user_id = f"row{row_index}_{profile.age}岁{profile.sex}_{timestamp}"
    else:
        user_id = f"{profile.age}岁{profile.sex}_{timestamp}"
    
    # 1. 保存完整JSON结果
    json_path = os.path.join(output_dir, f"result_{user_id}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        # 将UserProfile转换为dict
        results_copy = results.copy()
        results_copy['profile'] = asdict(profile)
        if row_index is not None:
            results_copy['row_index'] = row_index
        json.dump(results_copy, f, ensure_ascii=False, indent=2)
    
    # 2. 保存Markdown报告
    report_path = os.path.join(output_dir, f"report_{user_id}.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        # 在报告开头添加数据来源信息
        if row_index is not None:
            f.write(f"<!-- 数据来源：第{row_index}行 -->\n\n")
        f.write(results['report'])
    
    print(f"\n✅ 结果已保存：")
    print(f"   - JSON: {json_path}")
    print(f"   - 报告: {report_path}")
    
    return json_path, report_path


# ============ 批量处理工具 ============
def batch_process(profiles: List[UserProfile], output_dir: str = "./output", 
                  verbose: bool = False, save_reports: bool = True) -> List[Dict]:
    """
    批量处理多个用户画像
    
    参数:
        profiles: UserProfile列表
        output_dir: 输出目录
        verbose: 是否显示详细过程
        save_reports: 是否保存报告
    
    返回:
        所有评估结果的列表
    """
    os.makedirs(output_dir, exist_ok=True)
    
    orchestrator = OrchestratorAgentV2()
    all_results = []
    
    print(f"\n开始批量处理 {len(profiles)} 个用户...")
    print("=" * 60)
    
    for i, profile in enumerate(profiles):
        print(f"\n处理第 {i+1}/{len(profiles)} 个用户...")
        print(f"用户信息: {profile.age}岁 {profile.sex}")
        
        try:
            # 运行评估
            results = orchestrator.run(profile, verbose=verbose)
            
            # 保存结果
            if save_reports:
                save_results(results, profile, output_dir=output_dir)
            
            all_results.append({
                'index': i,
                'profile': asdict(profile),
                'results': results,
                'success': True
            })
            
            print(f"✅ 第 {i+1} 个用户评估完成")
            
        except Exception as e:
            print(f"❌ 第 {i+1} 个用户评估失败: {str(e)}")
            all_results.append({
                'index': i,
                'profile': asdict(profile),
                'error': str(e),
                'success': False
            })
    
    # 统计结果
    success_count = sum(1 for r in all_results if r['success'])
    print("\n" + "=" * 60)
    print(f"批量处理完成！")
    print(f"成功: {success_count}/{len(profiles)}")
    print(f"失败: {len(profiles) - success_count}/{len(profiles)}")
    print("=" * 60)
    
    # 保存汇总结果
    summary_path = os.path.join(output_dir, 'batch_summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        # 移除report字段以减小文件大小
        summary = []
        for r in all_results:
            r_copy = r.copy()
            if 'results' in r_copy and 'report' in r_copy['results']:
                r_copy['results'] = {k: v for k, v in r_copy['results'].items() if k != 'report'}
            summary.append(r_copy)
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"汇总结果已保存: {summary_path}")
    
    return all_results


# ============ 主程序入口 ============
def main():
    """主程序入口 - 演示如何使用系统"""
    
    print("=" * 80)
    print("AI 养老健康多 Agent 协作系统 V2.0")
    print("健康评估与照护行动计划生成系统")
    print("=" * 80)
    
    # 数据文件路径
    EXCEL_PATH = '../data/clhls_2018_bilingual_headers-checked.xlsx'
    OUTPUT_DIR = '../data/output_v2'
    
    # 检查文件是否存在
    if not os.path.exists(EXCEL_PATH):
        print(f"\n❌ 数据文件不存在: {EXCEL_PATH}")
        print("请检查文件路径")
        return
    
    # 用户选择运行模式
    print("\n请选择运行模式：")
    print("1. 单个样本测试（快速验证）")
    print("2. 批量处理50条数据（完整评估）")
    print("3. 自定义数量")
    
    choice = input("\n请输入选项 (1/2/3，默认为1): ").strip() or "1"
    
    if choice == "1":
        # 模式1：单个样本测试
        print("\n【模式1：单个样本测试】")
        print("=" * 60)
        
        try:
            profile = load_user_profile_from_excel(EXCEL_PATH, row_index=0)
            print(f"✅ 成功加载用户数据：{profile.age}岁 {profile.sex}")
            
            # 创建调度中心并运行
            orchestrator = OrchestratorAgentV2()
            results = orchestrator.run(profile, verbose=True)
            
            # 保存结果
            save_results(results, profile, output_dir=OUTPUT_DIR)
            
            print("\n" + "=" * 60)
            print("✅ 评估完成！")
            print("=" * 60)
            
        except Exception as e:
            print(f"❌ 评估失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    elif choice == "2":
        # 模式2：批量处理50条
        print("\n【模式2：批量处理50条数据】")
        print("=" * 60)
        
        try:
            profiles = load_multiple_profiles(EXCEL_PATH, n_samples=50, random_state=42)
            
            # 批量处理
            all_results = batch_process(
                profiles, 
                output_dir=OUTPUT_DIR,
                verbose=False,  # 批量处理时不显示详细过程
                save_reports=True
            )
            
            print("\n✅ 批量处理完成！")
            
        except Exception as e:
            print(f"❌ 批量处理失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    elif choice == "3":
        # 模式3：自定义数量
        n_samples = int(input("\n请输入要处理的样本数量: ").strip())
        
        print(f"\n【模式3：批量处理{n_samples}条数据】")
        print("=" * 60)
        
        try:
            profiles = load_multiple_profiles(EXCEL_PATH, n_samples=n_samples, random_state=42)
            
            # 批量处理
            all_results = batch_process(
                profiles, 
                output_dir=OUTPUT_DIR,
                verbose=False,
                save_reports=True
            )
            
            print("\n✅ 批量处理完成！")
            
        except Exception as e:
            print(f"❌ 批量处理失败: {str(e)}")
            import traceback
            traceback.print_exc()
    
    else:
        print("❌ 无效的选项")


def test_single_sample():
    """快速测试单个样本（用于调试）"""
    print("=" * 60)
    print("快速测试模式")
    print("=" * 60)
    
    # 创建测试用户
    test_profile = UserProfile(
        age=85,
        sex="女",
        province="河南",
        residence="农村",
        education_years=0,
        marital_status="丧偶",
        
        # 健康限制
        health_limitation="是，有些限制",
        
        # BADL - 基本自理
        badl_bathing="不需要帮助",
        badl_dressing="不需要帮助",
        badl_toileting="不需要帮助",
        badl_transferring="不需要帮助",
        badl_continence="不需要帮助",
        badl_eating="不需要帮助",
        
        # IADL - 部分困难
        iadl_visiting="能",
        iadl_shopping="有点困难",
        iadl_cooking="能",
        iadl_laundry="有点困难",
        iadl_walking="有点困难",
        iadl_carrying="不能做",
        iadl_crouching="不能做",
        iadl_transport="不能做",
        
        # 慢性病
        hypertension="否",
        diabetes="否",
        heart_disease="是",
        stroke="否",
        arthritis="是",
        
        # 认知功能
        cognition_time="正确",
        cognition_month="正确",
        cognition_season="正确",
        cognition_place="正确",
        cognition_calc=["正确", "正确", "错误"],
        
        # 心理状态
        depression="有时",
        anxiety="很少",
        loneliness="有时",
        
        # 生活方式
        smoking="从不",
        drinking="从不",
        exercise="有时",
        sleep_quality="一般",
        
        # 生理指标
        weight=50,
        height=155,
        vision="一般",
        hearing="好",
        
        # 社会支持
        living_arrangement="与子女同住",
        cohabitants=3,
        medical_insurance="城乡居民医保",
        caregiver="子女",
        financial_status="一般",
        
        user_type="elderly"
    )
    
    # 创建调度中心并运行
    orchestrator = OrchestratorAgentV2()
    results = orchestrator.run(test_profile, verbose=True)
    
    # 保存结果
    save_results(results, test_profile, output_dir="../data/output_v2_test")
    
    print("\n✅ 测试完成！")
    return results


if __name__ == "__main__":
    main()
