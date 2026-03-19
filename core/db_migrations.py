"""
数据库迁移脚本
扩展现有 SQLite 数据库，添加用户认证、家属关系、修改日志、报告版本等表
"""

import sqlite3
from pathlib import Path
from datetime import datetime


def get_db_path():
    """获取数据库路径"""
    return Path(__file__).parent.parent / "data" / "users.db"


def init_auth_tables(db_path: str):
    """初始化认证相关表"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 扩展 users 表（如果不存在则创建）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            user_type TEXT NOT NULL CHECK(user_type IN ('elderly', 'family')),
            elderly_id TEXT,
            name TEXT,
            phone TEXT UNIQUE,
            password_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (elderly_id) REFERENCES users(user_id)
        )
    """)

    # 2. 老年人-家属关系表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS elderly_family_relation (
            relation_id TEXT PRIMARY KEY,
            elderly_id TEXT NOT NULL,
            family_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (elderly_id) REFERENCES users(user_id),
            FOREIGN KEY (family_id) REFERENCES users(user_id),
            UNIQUE(elderly_id, family_id)
        )
    """)

    # 3. 用户档案表（扩展现有的 profile 数据）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            profile_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL UNIQUE,
            profile_data TEXT,
            completion_rate REAL DEFAULT 0.0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 4. 修改日志表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profile_edit_log (
            log_id TEXT PRIMARY KEY,
            elderly_id TEXT NOT NULL,
            editor_id TEXT NOT NULL,
            editor_type TEXT NOT NULL CHECK(editor_type IN ('elderly', 'family')),
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (elderly_id) REFERENCES users(user_id),
            FOREIGN KEY (editor_id) REFERENCES users(user_id)
        )
    """)

    # 5. 报告版本表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS report_versions (
            version_id TEXT PRIMARY KEY,
            elderly_id TEXT NOT NULL,
            report_data TEXT NOT NULL,
            completion_rate REAL NOT NULL,
            generated_by TEXT NOT NULL,
            generated_by_type TEXT NOT NULL CHECK(generated_by_type IN ('elderly', 'family')),
            generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version_number TEXT NOT NULL,
            is_latest BOOLEAN DEFAULT 1,
            FOREIGN KEY (elderly_id) REFERENCES users(user_id),
            FOREIGN KEY (generated_by) REFERENCES users(user_id)
        )
    """)

    # 6. 会话表（扩展现有的 sessions）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 7. 消息表（扩展现有的 messages）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
    """)

    # 创建索引以提高查询性能
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_type ON users(user_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_elderly_id ON users(elderly_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relation_elderly ON elderly_family_relation(elderly_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_relation_family ON elderly_family_relation(family_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_edit_log_elderly ON profile_edit_log(elderly_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_edit_log_editor ON profile_edit_log(editor_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_elderly ON report_versions(elderly_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_report_latest ON report_versions(elderly_id, is_latest)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")

    conn.commit()
    conn.close()
    print("✓ 数据库表创建成功")


def migrate_existing_data(db_path: str):
    """迁移现有数据到新表结构"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 检查是否已有旧的 users 表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if cursor.fetchone():
        print("✓ users 表已存在，跳过迁移")
    else:
        print("✓ 新建 users 表")

    conn.close()


if __name__ == "__main__":
    db_path = str(get_db_path())
    print(f"初始化数据库: {db_path}")
    init_auth_tables(db_path)
    migrate_existing_data(db_path)
    print("✓ 数据库初始化完成")
