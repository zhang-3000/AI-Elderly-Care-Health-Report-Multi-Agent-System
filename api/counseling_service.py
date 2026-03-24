"""
心理咨询（情感陪伴）服务。

提供多轮对话式心理咨询功能，调用 DeepSeek LLM，
支持同步与流式两种响应模式，对话历史持久化到 SQLite。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# ── LLM 配置 ─────────────────────────────────────────────

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT_SECONDS = max(float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "180")), 1.0)

# 上下文窗口：发送给 LLM 的最大历史消息数（不含 system prompt）
MAX_HISTORY_MESSAGES = 40

# ── 系统提示词（占位，可通过环境变量覆盖）──────────────────

_DEFAULT_SYSTEM_PROMPT = """\
你是一位温暖、耐心、经验丰富的心理咨询师，专门为老年人提供情感陪伴与心理支持。你的目标是帮助老年人缓解孤独、焦虑、抑郁等情绪，增强他们的安全感和价值感。

### 重要信息：老年人已知背景资料
在本次对话开始前，系统已经通过健康评估服务获取了这位老人的以下信息（作为本次对话的初始上下文）。你应当充分理解并善用这些信息，避免重复询问已经了解过的内容，而是基于这些信息开展更贴心、个性化的交流。

【关于已知信息中的特殊值说明】
部分字段的值可能为“不适用”、“无法回答”、“99”、“888”等，这表示该项信息缺失或无法获取。当你看到这些值时，直接忽略该字段，禁止追问老人，禁止用这些值推断其状况。

已知信息包括（以结构化数据形式提供）：
- 基本信息（年龄、性别、居住地、教育程度、婚姻状况）
- 身体指标（身高、体重、视力、听力、腰围、臀围）
- 健康限制（日常活动受影响程度）
- 日常活动能力（BADL、IADL各项）
- 慢性病史（已诊断疾病列表及补充说明）
- 认知功能（定向力、计算能力等测试结果）
- 心理状态（近两周抑郁、焦虑、孤单的频率）
- 生活方式（吸烟、饮酒、运动、睡眠）
- 社会支持（同住人、主要照顾者、经济状况、医保情况）

**使用这些信息的原则：**
1. **不要直接复述所有信息**，那样会让老人觉得被调查。
2. **在开场时可以自然提及**：例如“我看到您之前提到自己平时喜欢散步，最近天气好，有没有出去走走呀？”
3. **对于健康状况**（如慢性病、疼痛），表达关心时可以说：“您之前提到有高血压，最近有没有按时量血压？医生怎么说？” 绝不替代医生诊断。
4. **对于情绪问题**，如果已知老人最近感到孤单或情绪低落，可以温和地开启话题：“您上次说有时候会感到孤单，今天咱们多聊聊，您愿意跟我说说最近怎么样吗？”
5. **如果某些信息已明确**（如独居、老伴去世），不要反复追问，而是关注其带来的心理感受：“一个人住，平时会不会觉得家里太安静？”
6. **对于能力受限**（如听力差、行动不便），调整沟通方式：说话清晰、放慢语速；关心是否需要帮助。

### 沟通原则
1.  **语言风格**：使用简单、亲切、通俗的词汇，放慢语速感，避免专业术语。多使用“咱们”、“慢慢说”、“我听着呢”等表达，营造安全氛围。
2.  **倾听优先**：先让老人充分表达，通过复述和共情（“听起来您最近有些孤单”）让他们感到被理解。
3.  **关注多维信息**：在对话中自然了解老人的生活状况、疾病情况、心理状态，但已知信息不必重复询问，而是以此为背景进行深入交流。
4.  **引导与支持**：
    - 用开放式问题（“您最近一天是怎么过的？”）代替封闭式提问。
    - 当发现情绪低落时，先共情，再尝试引导回忆积极经历或寻找身边支持资源。
    - 若老人提及健康问题，强调“您有按时看医生吗？”“医生怎么说？”——不提供医疗建议，但鼓励遵从医嘱。
    - 如果出现严重抑郁、自杀意念或急性心理危机，必须温和地建议联系家人或拨打心理援助热线（如北京24小时心理援助热线：010-82951332），并告诉对方“您不是一个人，我会陪着您，但专业医生能给您更好的帮助。”
5.  **尊重自主性**：不强迫老人接受观点，肯定他们的经验和智慧（“您经历得多，一定有自己的办法”）。

### 对话示例（体现已知信息的使用）
- **已知信息**：老人 78岁，独居，有高血压和关节炎，日常活动能力中“洗澡、走路需要帮助”，近两周感到孤单。
  **开场**：“您好呀，我看到您平时一个人住，腿脚也不太方便，今天身体感觉怎么样？有没有按时吃药呀？”
- **已知信息**：老人听力一般，有轻度认知下降（时间定向力差）。
  **沟通**：（说话清晰、放慢）王奶奶，咱们慢慢聊。您还记得今天是星期几吗？没关系，想不起来也很正常，咱们今天聊点开心的。
- **已知信息**：老人近两周经常觉得心里发紧、爱担心。
  **回应**：“您之前说最近总有点不踏实，这其实很多老人都会经历。今天咱们专门聊聊，您具体担心什么呢？我们一起想想办法。”

请始终以温和、真诚的态度，成为老人可以信任的倾听者。
"""

COUNSELING_SYSTEM_PROMPT = os.getenv("COUNSELING_SYSTEM_PROMPT", _DEFAULT_SYSTEM_PROMPT)


# 在 CounselingService 类定义之前添加

def get_elderly_profile(user_id: str) -> Optional[dict]:
    """
    根据 user_id 从慢病管理服务获取老年人的完整画像数据。
    返回包含所有已知字段的字典。
    若不存在则返回 None
    """
    # 示例实现：从数据库读取，或调用远程API
    # 这里仅做占位，你需要根据实际情况替换
    profile = {}
    return profile
        # ... 62个字段的字典

class CounselingService:
    """心理咨询服务：管理咨询会话、调用 LLM、持久化消息。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        self._init_db()

    # ── 数据库 ────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counseling_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id    TEXT NOT NULL,
                    title      TEXT NOT NULL DEFAULT '心理咨询',
                    status     TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_counseling_sessions_user
                ON counseling_sessions(user_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counseling_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES counseling_sessions(session_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_counseling_messages_session
                ON counseling_messages(session_id)
                """
            )

    # ── 会话管理 ──────────────────────────────────────────

    def create_session(self, user_id: str) -> Dict[str, Any]:
        """创建新的咨询会话，插入 system 消息。"""
        session_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO counseling_sessions (session_id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, user_id, now, now),
            )
            conn.execute(
                "INSERT INTO counseling_messages (message_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, session_id, "system", COUNSELING_SYSTEM_PROMPT, now),
            )

        return {"session_id": session_id, "created_at": now}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话元数据。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM counseling_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def list_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """列出用户的咨询会话。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM counseling_sessions WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_sessions(self) -> List[Dict[str, Any]]:
        """列出所有咨询会话（医生视角）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM counseling_sessions ORDER BY updated_at DESC",
            ).fetchall()
        return [dict(r) for r in rows]

    def end_session(self, session_id: str) -> bool:
        """结束会话。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE counseling_sessions SET status = 'ended', updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
        return cursor.rowcount > 0

    # ── 消息历史 ──────────────────────────────────────────

    def _load_messages(self, session_id: str) -> List[Dict[str, str]]:
        """加载会话全部消息（含 system）。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM counseling_messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def _save_message(self, session_id: str, role: str, content: str) -> str:
        """持久化一条消息，返回 message_id。"""
        message_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO counseling_messages (message_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, session_id, role, content, now),
            )
            conn.execute(
                "UPDATE counseling_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
        return message_id

    def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """返回消息列表（排除 system），供前端展示。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT message_id, role, content, created_at FROM counseling_messages "
                "WHERE session_id = ? AND role != 'system' ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── LLM 调用 ─────────────────────────────────────────

    def _format_profile_text(self, profile: dict) -> str:
        """将老人画像字典转换为文字描述，包含所有字段，AI会自行忽略缺失值"""
        lines = []
        lines.append("【以下是这位老人的已知背景信息，供你在对话中使用】")
        lines.append("（注：部分值可能为“不适用”、“无法回答”、“99”、“888”等，代表信息缺失，请忽略）")

        # 遍历所有字段，输出“字段名：字段值”
        for key, value in profile.items():

            # 处理列表类型的字段
            if isinstance(value, list):
                value_str = ', '.join(str(v) for v in value)
            else:
                value_str = str(value)

            lines.append(f"- {key}：{value_str}")

        lines.append("\n请在对话中充分利用这些信息，避免重复询问已知内容，并基于此提供个性化关怀。")
        return "\n".join(lines)

    def _build_llm_messages(self, session_id: str, history: List[Dict[str, str]], new_user_message: str) -> List[Dict[str, str]]:
        """构造发送给 LLM 的消息列表，包含窗口截断。"""
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"会话不存在: {session_id}")
        user_id = session["user_id"]

        profile = get_elderly_profile(user_id)          # 调用顶层函数

        system_msg = {"role": "system", "content": COUNSELING_SYSTEM_PROMPT}
        messages = [system_msg]

        if profile:
            profile_text = self._format_profile_text(profile)
            context_msg = {"role": "system", "content": profile_text}
            messages.append(context_msg)

        context_msg = {"role": "system", "content": profile_text}

        # 从历史中剔除 system 消息
        non_system = [m for m in history if m["role"] != "system"]

        # 窗口截断：只保留最近 N 条
        if len(non_system) > MAX_HISTORY_MESSAGES:
            non_system = non_system[-MAX_HISTORY_MESSAGES:]
        messages.extend(non_system)

        messages.append({"role": "user", "content": new_user_message})

        return messages

    def send_message(self, session_id: str, content: str) -> Dict[str, Any]:
        """非流式发送消息：调用 LLM 并持久化。"""
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"咨询会话不存在: {session_id}")
        if session["status"] != "active":
            raise ValueError("该咨询会话已结束")

        history = self._load_messages(session_id)
        messages = self._build_llm_messages(session_id, history, content)

        # 持久化用户消息
        self._save_message(session_id, "user", content)

        # 调用 LLM
        try:
            response = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=1024,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            reply = response.choices[0].message.content or ""
        except Exception as exc:
            logger.exception("Counseling LLM call failed for session=%s", session_id)
            raise RuntimeError(f"LLM 调用失败: {exc}") from exc

        # 持久化助手回复
        now = datetime.now(timezone.utc).isoformat()
        message_id = self._save_message(session_id, "assistant", reply)

        return {
            "message_id": message_id,
            "role": "assistant",
            "content": reply,
            "created_at": now,
        }

    def send_message_stream(self, session_id: str, content: str) -> Generator[str, None, None]:
        """流式发送消息：yield 文本片段，完成后持久化完整回复。"""
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"咨询会话不存在: {session_id}")
        if session["status"] != "active":
            raise ValueError("该咨询会话已结束")

        history = self._load_messages(session_id)
        messages = self._build_llm_messages(session_id, history, content)

        # 持久化用户消息
        self._save_message(session_id, "user", content)

        # 流式调用 LLM
        full_reply_parts: list[str] = []
        try:
            response = self._client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=1024,
                stream=True,
                timeout=LLM_TIMEOUT_SECONDS,
            )
            for chunk in response:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    full_reply_parts.append(delta)
                    yield delta
        except Exception as exc:
            logger.exception("Counseling LLM stream failed for session=%s", session_id)
            raise RuntimeError(f"LLM 流式调用失败: {exc}") from exc

        # 持久化完整助手回复
        full_reply = "".join(full_reply_parts)
        if full_reply:
            self._save_message(session_id, "assistant", full_reply)