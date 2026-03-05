"""
用户画像持久化层
使用 SQLite 存储用户画像，支持跨会话保留
"""

import sqlite3
import json
import uuid
import os
from datetime import datetime
from typing import Optional, Dict, Any
from dataclasses import asdict, fields

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from multi_agent_system_v2 import UserProfile


# 默认数据库路径
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "users.db"
)


class UserProfileStore:
    """
    用户画像持久化存储
    - 每个用户用 UUID 标识
    - UserProfile 字段序列化为 JSON 存入 SQLite
    - 支持增量更新（只更新非 None 字段）
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ─────────────────────────────────────────────
    # 数据库初始化
    # ─────────────────────────────────────────────

    def _init_db(self):
        """初始化数据库表结构"""
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     TEXT PRIMARY KEY,
                    profile     TEXT NOT NULL,       -- UserProfile JSON
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                )
            """)
            # 对话历史表（短期记忆归档，可选）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id  TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    messages    TEXT NOT NULL,        -- list of {role, content} JSON
                    status      TEXT NOT NULL,        -- COLLECTING / DONE
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ─────────────────────────────────────────────
    # 用户 CRUD
    # ─────────────────────────────────────────────

    def create_user(self) -> str:
        """
        创建新用户，返回 user_id (UUID)
        初始 UserProfile 全部字段为 None
        """
        user_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        empty_profile = asdict(UserProfile())

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (user_id, profile, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, json.dumps(empty_profile, ensure_ascii=False), now, now)
            )
        return user_id

    def get_profile(self, user_id: str) -> Optional[UserProfile]:
        """根据 user_id 加载 UserProfile，不存在返回 None"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT profile FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

        if row is None:
            return None

        data = json.loads(row["profile"])
        return self._dict_to_profile(data)

    def update_profile(self, user_id: str, updates: Dict[str, Any]) -> UserProfile:
        """
        增量更新用户画像
        只更新 updates 中不为 None 的字段，不覆盖已有数据
        返回更新后的 UserProfile
        """
        profile = self.get_profile(user_id)
        if profile is None:
            raise ValueError(f"用户 {user_id} 不存在")

        profile_dict = asdict(profile)

        # 增量合并：只写入新值，不覆盖已有非 None 值
        # 如果想允许覆盖，把 if 条件去掉即可
        changed = False
        for key, value in updates.items():
            if key in profile_dict and value is not None:
                # cognition_calc 是 list，特殊处理：合并非空项
                if key == "cognition_calc" and isinstance(value, list):
                    old = profile_dict.get("cognition_calc") or ["", "", ""]
                    merged = [
                        v if v not in (None, "") else old[i]
                        for i, v in enumerate(value[:3])
                    ]
                    if merged != old:
                        profile_dict[key] = merged
                        changed = True
                else:
                    if profile_dict[key] != value:
                        profile_dict[key] = value
                        changed = True

        if changed:
            now = datetime.now().isoformat()
            with self._conn() as conn:
                conn.execute(
                    "UPDATE users SET profile = ?, updated_at = ? WHERE user_id = ?",
                    (json.dumps(profile_dict, ensure_ascii=False), now, user_id)
                )

        return self._dict_to_profile(profile_dict)

    def save_profile(self, user_id: str, profile: UserProfile):
        """完整覆盖保存 UserProfile"""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET profile = ?, updated_at = ? WHERE user_id = ?",
                (json.dumps(asdict(profile), ensure_ascii=False), now, user_id)
            )

    def user_exists(self, user_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row is not None

    def list_users(self) -> list:
        """列出所有 user_id"""
        with self._conn() as conn:
            rows = conn.execute("SELECT user_id, updated_at FROM users ORDER BY updated_at DESC").fetchall()
        return [{"user_id": r["user_id"], "updated_at": r["updated_at"]} for r in rows]

    # ─────────────────────────────────────────────
    # 会话（Session）管理
    # ─────────────────────────────────────────────

    def create_session(self, user_id: str) -> str:
        """为用户创建新会话，返回 session_id"""
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, messages, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, user_id, json.dumps([]), "COLLECTING", now, now)
            )
        return session_id

    def get_session_messages(self, session_id: str) -> list:
        """获取会话的对话历史"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT messages FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return []
        return json.loads(row["messages"])

    def get_session(self, session_id: str) -> Optional[Dict]:
        """根据 session_id 获取会话信息"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def append_message(self, session_id: str, role: str, content: str):
        """向会话追加一条消息，role = 'user' | 'assistant'"""
        messages = self.get_session_messages(session_id)
        messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET messages = ?, updated_at = ? WHERE session_id = ?",
                (json.dumps(messages, ensure_ascii=False), now, session_id)
            )

    def update_session_status(self, session_id: str, status: str):
        """更新会话状态"""
        now = datetime.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status, now, session_id)
            )

    def get_latest_session(self, user_id: str) -> Optional[Dict]:
        """获取用户最新会话"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ─────────────────────────────────────────────
    # 缺失字段分析
    # ─────────────────────────────────────────────

    def get_missing_fields(self, user_id: str) -> Dict[str, list]:
        """
        返回 UserProfile 中各分组的缺失字段
        格式: { "group_name": ["field1", "field2", ...] }
        """
        profile = self.get_profile(user_id)
        if profile is None:
            return {}

        profile_dict = asdict(profile)

        # 按分组定义字段
        FIELD_GROUPS = {
            "基本信息": ["age", "sex", "province", "residence", "education_years", "marital_status"],
            "健康限制": ["health_limitation"],
            "日常活动（BADL）": [
                "badl_bathing", "badl_dressing", "badl_toileting",
                "badl_transferring", "badl_continence", "badl_eating"
            ],
            "日常活动（IADL）": [
                "iadl_visiting", "iadl_shopping", "iadl_cooking", "iadl_laundry",
                "iadl_walking", "iadl_carrying", "iadl_crouching", "iadl_transport"
            ],
            "慢性病情况": [
                "hypertension", "diabetes", "heart_disease", "stroke",
                "cataract", "cancer", "arthritis"
            ],
            "认知功能": [
                "cognition_time", "cognition_month", "cognition_season",
                "cognition_place", "cognition_calc", "cognition_draw"
            ],
            "心理状态": ["depression", "anxiety", "loneliness"],
            "生活方式": ["smoking", "drinking", "exercise", "sleep_quality"],
            "身体指标": ["weight", "height", "vision", "hearing"],
            "社会支持": [
                "living_arrangement", "cohabitants", "financial_status",
                "income", "medical_insurance", "caregiver"
            ],
        }

        missing = {}
        for group, field_list in FIELD_GROUPS.items():
            empty = []
            for f in field_list:
                val = profile_dict.get(f)
                # cognition_calc 是 list，特殊判断
                if f == "cognition_calc":
                    if not val or all(v in (None, "") for v in val):
                        empty.append(f)
                elif val is None:
                    empty.append(f)
            if empty:
                missing[group] = empty

        return missing

    def is_profile_complete(self, user_id: str) -> bool:
        """判断用户画像是否已经全部填完"""
        return len(self.get_missing_fields(user_id)) == 0

    def get_completion_rate(self, user_id: str) -> float:
        """返回画像完成度 0.0 ~ 1.0"""
        profile = self.get_profile(user_id)
        if profile is None:
            return 0.0
        profile_dict = asdict(profile)
        # 排除 user_type（系统字段，不计入）
        all_fields = [k for k in profile_dict if k != "user_type"]
        total = len(all_fields)
        filled = 0
        for k in all_fields:
            v = profile_dict[k]
            if k == "cognition_calc":
                if v and any(x not in (None, "") for x in v):
                    filled += 1
            elif v is not None:
                filled += 1
        return filled / total if total > 0 else 0.0

    # ─────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────

    @staticmethod
    def _dict_to_profile(data: dict) -> UserProfile:
        """将 dict 转回 UserProfile，过滤掉 dataclass 不认识的 key"""
        valid_keys = {f.name for f in fields(UserProfile)}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return UserProfile(**filtered)
