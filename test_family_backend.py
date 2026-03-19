#!/usr/bin/env python3
"""
家属端后端测试脚本
测试家属端的完整流程
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "code"))

from memory.family_caregiver_manager import FamilyCaregiverManager
from memory.user_profile_store import UserProfileStore


def test_family_caregiver_flow():
    """测试家属端完整流程"""
    
    print("=" * 60)
    print("🧪 家属端后端测试")
    print("=" * 60)
    
    # 1. 初始化
    print("\n📦 步骤 1: 初始化管理器...")
    db_path = "/tmp/elderly-care-db/users.db"
    manager = FamilyCaregiverManager(db_path=db_path)
    store = manager.store
    print("✓ 管理器初始化完成")
    
    # 2. 获取老人列表
    print("\n📦 步骤 2: 获取老人列表...")
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        print("✗ 数据库中没有老人数据，请先运行 generate_test_data.py")
        return False
    
    elderly_id = row[0]
    print(f"✓ 找到老人: {elderly_id}")
    
    # 3. 启动家属端会话
    print("\n📦 步骤 3: 启动家属端会话...")
    try:
        session_id = manager.new_family_session(elderly_id)
        print(f"✓ 会话启动成功: {session_id}")
    except Exception as e:
        print(f"✗ 会话启动失败: {e}")
        return False
    
    # 4. 获取会话信息
    print("\n📦 步骤 4: 获取会话信息...")
    try:
        info = manager.get_session_info(session_id)
        print(f"✓ 会话状态: {info['state']}")
        print(f"✓ 进度: {info['progress']:.0%}")
    except Exception as e:
        print(f"✗ 获取会话信息失败: {e}")
        return False
    
    # 5. 模拟对话
    print("\n📦 步骤 5: 模拟对话流程...")
    
    test_messages = [
        "好的，我准备好了",
        "我是他的女儿",
        "是的，我是主要照护者",
        "我们同住，日常经常照顾",
        "主要是我观察到的",
        "他今年82岁，男性，住在北京，城市",
        "最近走路变慢了，记性也变差了",
        "洗澡需要部分帮助，穿衣也需要帮助",
        "他有高血压和糖尿病",
        "最近没有住院",
        "最近3个月没有跌倒",
        "没有失禁",
        "没有明显体重下降",
        "没有吞咽困难",
        "精神状态还可以",
        "没有出现迷路或认错人",
        "睡眠质量一般，有时候夜里会醒",
        "一个人出门我有点担心，怕他走丢",
        "情绪还可以，就是有时候会有点低落",
        "是的，比以前更容易忘事",
        "他在吃降压药和降糖药",
        "吃药还比较规律，我会提醒他",
        "最困难的是他不太愿意配合锻炼",
        "希望能得到一些照护技巧和安全建议",
        "是的，信息都正确"
    ]
    
    for i, msg in enumerate(test_messages, 1):
        try:
            result = manager.chat(session_id, msg)
            print(f"\n  消息 {i}: {msg[:30]}...")
            print(f"  状态: {result['state']}")
            print(f"  进度: {result['progress']:.0%}")
            
            if result['state'] == 'GENERATING':
                print("  ✓ 已进入报告生成阶段")
                break
        except Exception as e:
            print(f"  ✗ 处理消息失败: {e}")
            return False
    
    print("\n" + "=" * 60)
    print("✅ 家属端后端测试完成！")
    print("=" * 60)
    
    return True


if __name__ == "__main__":
    success = test_family_caregiver_flow()
    sys.exit(0 if success else 1)
