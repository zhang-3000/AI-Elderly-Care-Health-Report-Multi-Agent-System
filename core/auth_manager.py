"""
用户认证管理模块
处理用户登录、注册、JWT token 生成等
"""

import hashlib
import secrets
import sqlite3
import uuid
import json
import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

# 简单的 JWT 实现（不依赖外部库）
class SimpleJWT:
    @staticmethod
    def encode(payload: Dict, secret: str) -> str:
        """简单的 JWT 编码"""
        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip('=')
        payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        
        message = f"{header_b64}.{payload_b64}"
        signature = hashlib.sha256(f"{message}{secret}".encode()).hexdigest()[:43]
        
        return f"{message}.{signature}"
    
    @staticmethod
    def decode(token: str, secret: str) -> Optional[Dict]:
        """简单的 JWT 解码"""
        try:
            parts = token.split('.')
            if len(parts) != 3:
                return None
            
            header_b64, payload_b64, signature = parts
            
            # 验证签名
            message = f"{header_b64}.{payload_b64}"
            expected_sig = hashlib.sha256(f"{message}{secret}".encode()).hexdigest()[:43]
            
            if signature != expected_sig:
                return None
            
            # 解码 payload
            payload_json = base64.urlsafe_b64decode(payload_b64 + '==')
            payload = json.loads(payload_json)
            
            # 检查过期时间
            if 'exp' in payload:
                exp_time = datetime.fromisoformat(payload['exp'])
                if datetime.utcnow() > exp_time:
                    return None
            
            return payload
        except Exception:
            return None


class AuthManager:
    """用户认证管理器"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "users.db")
        self.db_path = db_path
        self.secret_key = self._get_or_create_secret_key()

    def _get_or_create_secret_key(self) -> str:
        """获取或创建 JWT 密钥"""
        secret_file = Path("/tmp/elderly-care-db/.secret_key")
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        
        if secret_file.exists():
            return secret_file.read_text().strip()
        else:
            secret_key = secrets.token_urlsafe(32)
            secret_file.write_text(secret_key)
            return secret_key

    def _hash_password(self, password: str) -> str:
        """密码哈希"""
        return hashlib.sha256(password.encode()).hexdigest()

    def register_user(
        self,
        user_type: str,
        name: str,
        phone: str,
        password: str,
        elderly_id: Optional[str] = None,
        relation: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        注册新用户

        Args:
            user_type: 'elderly' 或 'family'
            name: 用户姓名
            phone: 手机号
            password: 密码
            elderly_id: 如果是家属，需要关联的老年人ID
            relation: 如果是家属，与老年人的关系（子女/配偶/其他）

        Returns:
            (成功标志, 消息, user_id)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 检查手机号是否已存在
            cursor.execute("SELECT user_id FROM users WHERE phone = ?", (phone,))
            if cursor.fetchone():
                return False, "手机号已被注册", None

            # 如果是家属，检查老年人是否存在
            if user_type == "family":
                if not elderly_id:
                    return False, "家属用户必须关联老年人", None
                cursor.execute("SELECT user_id FROM users WHERE user_id = ? AND user_type = 'elderly'", (elderly_id,))
                if not cursor.fetchone():
                    return False, "关联的老年人不存在", None

            # 创建用户
            user_id = str(uuid.uuid4())
            password_hash = self._hash_password(password)

            cursor.execute("""
                INSERT INTO users (user_id, user_type, elderly_id, name, phone, password_hash)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, user_type, elderly_id, name, phone, password_hash))

            # 如果是家属，创建关系记录
            if user_type == "family" and elderly_id:
                relation_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO elderly_family_relation (relation_id, elderly_id, family_id, relation)
                    VALUES (?, ?, ?, ?)
                """, (relation_id, elderly_id, user_id, relation or "家属"))

            # 如果是老年人，创建空的用户档案
            if user_type == "elderly":
                profile_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO user_profiles (profile_id, user_id, profile_data, completion_rate)
                    VALUES (?, ?, '{}', 0.0)
                """, (profile_id, user_id))

            conn.commit()
            return True, "注册成功", user_id

        except Exception as e:
            conn.rollback()
            return False, f"注册失败: {str(e)}", None
        finally:
            conn.close()

    def login(self, phone: str, password: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        用户登录

        Args:
            phone: 手机号
            password: 密码

        Returns:
            (成功标志, 消息, 用户信息字典)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            password_hash = self._hash_password(password)

            cursor.execute("""
                SELECT user_id, user_type, elderly_id, name, phone
                FROM users
                WHERE phone = ? AND password_hash = ?
            """, (phone, password_hash))

            user = cursor.fetchone()
            if not user:
                return False, "手机号或密码错误", None

            user_id, user_type, elderly_id, name, phone = user

            # 生成 JWT token
            token = self.generate_token(user_id, user_type)

            user_info = {
                "user_id": user_id,
                "user_type": user_type,
                "elderly_id": elderly_id,
                "name": name,
                "phone": phone,
                "token": token
            }

            return True, "登录成功", user_info

        except Exception as e:
            return False, f"登录失败: {str(e)}", None
        finally:
            conn.close()

    def generate_token(self, user_id: str, user_type: str, expires_in_days: int = 30) -> str:
        """生成 JWT token"""
        payload = {
            "user_id": user_id,
            "user_type": user_type,
            "exp": (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat(),
            "iat": datetime.utcnow().isoformat()
        }
        return SimpleJWT.encode(payload, self.secret_key)

    def verify_token(self, token: str) -> Tuple[bool, Optional[Dict]]:
        """验证 JWT token"""
        payload = SimpleJWT.decode(token, self.secret_key)
        if payload is None:
            return False, None
        return True, payload

    def get_user_info(self, user_id: str) -> Optional[Dict]:
        """获取用户信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT user_id, user_type, elderly_id, name, phone, created_at
                FROM users
                WHERE user_id = ?
            """, (user_id,))

            user = cursor.fetchone()
            if not user:
                return None

            return {
                "user_id": user[0],
                "user_type": user[1],
                "elderly_id": user[2],
                "name": user[3],
                "phone": user[4],
                "created_at": user[5]
            }

        finally:
            conn.close()

    def get_family_elderly_list(self, family_id: str) -> list:
        """获取家属关联的所有老年人列表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT 
                    u.user_id,
                    u.name,
                    r.relation,
                    up.completion_rate,
                    r.created_at
                FROM elderly_family_relation r
                JOIN users u ON r.elderly_id = u.user_id
                LEFT JOIN user_profiles up ON u.user_id = up.user_id
                WHERE r.family_id = ?
                ORDER BY r.created_at DESC
            """, (family_id,))

            elderly_list = []
            for row in cursor.fetchall():
                elderly_list.append({
                    "elderly_id": row[0],
                    "name": row[1],
                    "relation": row[2],
                    "completion_rate": row[3] or 0.0,
                    "created_at": row[4]
                })

            return elderly_list

        finally:
            conn.close()

    def check_family_access(self, family_id: str, elderly_id: str) -> bool:
        """检查家属是否有权限访问该老年人信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT 1 FROM elderly_family_relation
                WHERE family_id = ? AND elderly_id = ?
            """, (family_id, elderly_id))

            return cursor.fetchone() is not None

        finally:
            conn.close()

    def add_family_relation(
        self,
        elderly_id: str,
        family_id: str,
        relation: str
    ) -> Tuple[bool, str]:
        """添加家属关系"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 检查是否已存在关系
            cursor.execute("""
                SELECT 1 FROM elderly_family_relation
                WHERE elderly_id = ? AND family_id = ?
            """, (elderly_id, family_id))

            if cursor.fetchone():
                return False, "关系已存在"

            # 创建关系
            relation_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO elderly_family_relation (relation_id, elderly_id, family_id, relation)
                VALUES (?, ?, ?, ?)
            """, (relation_id, elderly_id, family_id, relation))

            conn.commit()
            return True, "添加成功"

        except Exception as e:
            conn.rollback()
            return False, f"添加失败: {str(e)}"
        finally:
            conn.close()


if __name__ == "__main__":
    # 测试代码
    auth = AuthManager()

    # 注册老年人
    success, msg, elderly_id = auth.register_user(
        user_type="elderly",
        name="张奶奶",
        phone="13800138000",
        password="123456"
    )
    print(f"注册老年人: {msg}, ID: {elderly_id}")

    # 注册家属
    if elderly_id:
        success, msg, family_id = auth.register_user(
            user_type="family",
            name="张小明",
            phone="13800138001",
            password="123456",
            elderly_id=elderly_id,
            relation="子女"
        )
        print(f"注册家属: {msg}, ID: {family_id}")

    # 登录测试
    success, msg, user_info = auth.login("13800138000", "123456")
    if success:
        print(f"登录成功: {user_info}")
        
        # 验证 token
        token = user_info["token"]
        valid, payload = auth.verify_token(token)
        print(f"Token 验证: {valid}, Payload: {payload}")
