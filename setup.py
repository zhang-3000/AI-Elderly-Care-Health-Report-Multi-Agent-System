"""
快速启动脚本
初始化数据库并创建测试用户
"""

import sys
from pathlib import Path

# 添加路径
sys.path.insert(0, str(Path(__file__).parent))

from core.db_migrations import init_auth_tables
from core.auth_manager import AuthManager


def setup_database():
    """初始化数据库"""
    # 使用 /tmp 目录避免权限问题
    db_path = "/tmp/elderly-care-db/users.db"
    
    print("=" * 60)
    print("🚀 AI 养老健康系统 - 数据库初始化")
    print("=" * 60)
    
    # 创建数据目录
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    # 初始化表
    print("\n📦 创建数据库表...")
    init_auth_tables(db_path)
    print("✓ 数据库表创建成功")
    
    return db_path


def create_test_users(db_path: str):
    """创建测试用户"""
    auth = AuthManager(db_path)
    
    print("\n👤 创建测试用户...")
    
    # 1. 创建老年人用户
    success, msg, elderly_id = auth.register_user(
        user_type="elderly",
        name="张奶奶",
        phone="13800138000",
        password="123456"
    )
    
    if success:
        print(f"✓ 老年人用户创建成功")
        print(f"  - 姓名: 张奶奶")
        print(f"  - 手机: 13800138000")
        print(f"  - 密码: 123456")
        print(f"  - ID: {elderly_id}")
        
        # 2. 创建家属用户
        success2, msg2, family_id = auth.register_user(
            user_type="family",
            name="张小明",
            phone="13800138001",
            password="123456",
            elderly_id=elderly_id,
            relation="子女"
        )
        
        if success2:
            print(f"\n✓ 家属用户创建成功")
            print(f"  - 姓名: 张小明")
            print(f"  - 手机: 13800138001")
            print(f"  - 密码: 123456")
            print(f"  - ID: {family_id}")
            print(f"  - 关联老年人: {elderly_id}")
        else:
            print(f"✗ 家属用户创建失败: {msg2}")
    else:
        print(f"✗ 老年人用户创建失败: {msg}")
    
    # 3. 创建第二个老年人用户
    success3, msg3, elderly_id_2 = auth.register_user(
        user_type="elderly",
        name="李爷爷",
        phone="13800138002",
        password="123456"
    )
    
    if success3:
        print(f"\n✓ 第二个老年人用户创建成功")
        print(f"  - 姓名: 李爷爷")
        print(f"  - 手机: 13800138002")
        print(f"  - 密码: 123456")
        print(f"  - ID: {elderly_id_2}")


def main():
    """主函数"""
    db_path = setup_database()
    create_test_users(db_path)
    
    print("\n" + "=" * 60)
    print("✅ 初始化完成！")
    print("=" * 60)
    print("\n📝 测试账号：")
    print("\n老年人账号 1:")
    print("  手机: 13800138000")
    print("  密码: 123456")
    print("\n家属账号:")
    print("  手机: 13800138001")
    print("  密码: 123456")
    print("\n老年人账号 2:")
    print("  手机: 13800138002")
    print("  密码: 123456")
    print("\n🚀 现在可以启动后端服务器:")
    print("  cd api && python server.py")
    print("\n🌐 然后启动前端:")
    print("  cd AI-Elderly-Care-Health-Report-Frontend && npm run dev")
    print("\n💡 访问 http://localhost:5173/login 开始使用")
    print("=" * 60)


if __name__ == "__main__":
    main()
