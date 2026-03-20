"""
认证存储与 token 服务。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


FAMILY_ROLE = "family"
ELDERLY_ROLE = "elderly"
DOCTOR_ROLE = "doctor"


@dataclass(frozen=True)
class AuthActor:
    subject_id: str
    role: str
    expires_at: str


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: str
    role: str
    subject_id: str


class TokenService:
    """无第三方依赖的简易签名 token。"""

    def __init__(self, secret_path: Path):
        self.secret_path = secret_path
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret = self._get_or_create_secret()

    def _get_or_create_secret(self) -> str:
        if self.secret_path.exists():
            return self.secret_path.read_text(encoding="utf-8").strip()
        secret = secrets.token_urlsafe(48)
        self.secret_path.write_text(secret, encoding="utf-8")
        return secret

    @staticmethod
    def _b64encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def issue(self, subject_id: str, role: str, expires_in_days: int) -> IssuedToken:
        issued_at = datetime.now(timezone.utc)
        expires_at = issued_at + timedelta(days=expires_in_days)
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": subject_id,
            "role": role,
            "iat": issued_at.isoformat(),
            "exp": expires_at.isoformat(),
        }

        header_token = self._b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        payload_token = self._b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        message = f"{header_token}.{payload_token}"
        signature = hmac.new(
            self.secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        token = f"{message}.{self._b64encode(signature)}"
        return IssuedToken(
            token=token,
            expires_at=payload["exp"],
            role=role,
            subject_id=subject_id,
        )

    def verify(self, token: str) -> Optional[AuthActor]:
        try:
            header_token, payload_token, signature_token = token.split(".")
        except ValueError:
            return None

        message = f"{header_token}.{payload_token}"
        expected_signature = hmac.new(
            self.secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(self._b64encode(expected_signature), signature_token):
            return None

        try:
            payload = json.loads(self._b64decode(payload_token).decode("utf-8"))
        except Exception:
            return None

        expires_at = payload.get("exp")
        role = str(payload.get("role") or "")
        subject_id = str(payload.get("sub") or "")
        if not expires_at or not role or not subject_id:
            return None

        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            return None
        if expiry <= datetime.now(timezone.utc):
            return None

        return AuthActor(subject_id=subject_id, role=role, expires_at=expires_at)


class AuthService:
    """家属账号、医生账号、绑定关系与 token 管理。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.token_service = TokenService(Path(db_path).parent / ".access_token_secret")
        self._init_db()
        self._init_default_doctor_account()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS family_accounts (
                    family_id TEXT PRIMARY KEY,
                    phone TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS doctor_accounts (
                    doctor_id TEXT PRIMARY KEY,
                    phone TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS family_elderly_relation (
                    relation_id TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL,
                    elderly_user_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(family_id, elderly_user_id),
                    FOREIGN KEY (elderly_user_id) REFERENCES users(user_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_family_accounts_phone ON family_accounts(phone)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_doctor_accounts_phone ON doctor_accounts(phone)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_family_relation_family ON family_elderly_relation(family_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_family_relation_elderly ON family_elderly_relation(elderly_user_id)"
            )

    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode("utf-8")).hexdigest()

    def _init_default_doctor_account(self) -> None:
        name = (os.getenv("DOCTOR_DEFAULT_NAME") or "").strip()
        phone = (os.getenv("DOCTOR_DEFAULT_PHONE") or "").strip()
        password = (os.getenv("DOCTOR_DEFAULT_PASSWORD") or "").strip()
        if not all([name, phone, password]):
            return

        password_hash = self._hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT doctor_id, created_at FROM doctor_accounts WHERE phone = ?",
                (phone,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO doctor_accounts (
                        doctor_id, phone, name, password_hash, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), phone, name, password_hash, now, now),
                )
                return

            conn.execute(
                """
                UPDATE doctor_accounts
                SET name = ?, password_hash = ?, updated_at = ?
                WHERE phone = ?
                """,
                (name, password_hash, now, phone),
            )

    def _elderly_exists(self, elderly_user_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?",
                (elderly_user_id,),
            ).fetchone()
        return row is not None

    def issue_elderly_token(self, elderly_user_id: str, expires_in_days: int = 180) -> IssuedToken:
        return self.token_service.issue(elderly_user_id, ELDERLY_ROLE, expires_in_days)

    def issue_family_token(self, family_id: str, expires_in_days: int = 30) -> IssuedToken:
        return self.token_service.issue(family_id, FAMILY_ROLE, expires_in_days)

    def issue_doctor_token(self, doctor_id: str, expires_in_days: int = 30) -> IssuedToken:
        return self.token_service.issue(doctor_id, DOCTOR_ROLE, expires_in_days)

    def verify_access_token(self, token: str) -> Optional[AuthActor]:
        return self.token_service.verify(token)

    def get_family_account(self, family_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT family_id, phone, name, created_at, updated_at
                FROM family_accounts
                WHERE family_id = ?
                """,
                (family_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_doctor_account(self, doctor_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT doctor_id, phone, name, created_at, updated_at
                FROM doctor_accounts
                WHERE doctor_id = ?
                """,
                (doctor_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_family_relations(self, family_id: str) -> list[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT elderly_user_id, relation, created_at
                FROM family_elderly_relation
                WHERE family_id = ?
                ORDER BY created_at DESC
                """,
                (family_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_family_elderly_ids(self, family_id: str) -> list[str]:
        return [item["elderly_user_id"] for item in self.list_family_relations(family_id)]

    def check_family_access(self, family_id: str, elderly_user_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM family_elderly_relation
                WHERE family_id = ? AND elderly_user_id = ?
                """,
                (family_id, elderly_user_id),
            ).fetchone()
        return row is not None

    def bind_family_to_elderly(
        self,
        family_id: str,
        elderly_user_id: str,
        relation: str = "家属",
    ) -> tuple[bool, str]:
        if not self._elderly_exists(elderly_user_id):
            return False, "关联的老年人不存在"

        relation_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO family_elderly_relation (
                        relation_id, family_id, elderly_user_id, relation, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (relation_id, family_id, elderly_user_id, relation, created_at),
                )
            return True, "绑定成功"
        except sqlite3.IntegrityError:
            return False, "绑定关系已存在"

    def register_family(
        self,
        name: str,
        phone: str,
        password: str,
        elderly_user_id: str,
        relation: str = "家属",
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        if not self._elderly_exists(elderly_user_id):
            return False, "关联的老年人不存在", None

        family_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        password_hash = self._hash_password(password)

        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO family_accounts (
                        family_id, phone, name, password_hash, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (family_id, phone, name, password_hash, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO family_elderly_relation (
                        relation_id, family_id, elderly_user_id, relation, created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), family_id, elderly_user_id, relation, now),
                )
        except sqlite3.IntegrityError:
            return False, "手机号已被注册", None

        issued_token = self.issue_family_token(family_id)
        return True, "注册成功", self._build_family_auth_payload(
            family_id=family_id,
            name=name,
            token=issued_token,
        )

    def authenticate_family(
        self,
        phone: str,
        password: str,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        password_hash = self._hash_password(password)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT family_id, phone, name
                FROM family_accounts
                WHERE phone = ? AND password_hash = ?
                """,
                (phone, password_hash),
            ).fetchone()

        if row is None:
            return False, "手机号或密码错误", None

        issued_token = self.issue_family_token(row["family_id"])
        return True, "登录成功", self._build_family_auth_payload(
            family_id=row["family_id"],
            name=row["name"],
            token=issued_token,
        )

    def authenticate_doctor(
        self,
        phone: str,
        password: str,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        password_hash = self._hash_password(password)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT doctor_id, phone, name
                FROM doctor_accounts
                WHERE phone = ? AND password_hash = ?
                """,
                (phone, password_hash),
            ).fetchone()

        if row is None:
            return False, "手机号或密码错误", None

        issued_token = self.issue_doctor_token(row["doctor_id"])
        return True, "登录成功", self._build_doctor_auth_payload(
            doctor_id=row["doctor_id"],
            name=row["name"],
            token=issued_token,
        )

    def _build_family_auth_payload(
        self,
        family_id: str,
        name: str,
        token: IssuedToken,
    ) -> Dict[str, Any]:
        elderly_ids = self.list_family_elderly_ids(family_id)
        return {
            "token": token.token,
            "expires_at": token.expires_at,
            "family_id": family_id,
            "user_name": name,
            "role": FAMILY_ROLE,
            "elderly_ids": elderly_ids,
        }

    def _build_doctor_auth_payload(
        self,
        doctor_id: str,
        name: str,
        token: IssuedToken,
    ) -> Dict[str, Any]:
        return {
            "token": token.token,
            "expires_at": token.expires_at,
            "user_name": name,
            "role": DOCTOR_ROLE,
            "elderly_ids": [],
        }
