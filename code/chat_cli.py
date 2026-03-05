#!/usr/bin/env python3
"""
命令行对话测试入口
用于本地测试记忆管理模块 + 多轮对话收集信息 + 自动触发报告生成

用法：
    cd code/
    python3 chat_cli.py              # 新用户
    python3 chat_cli.py --user <uuid> # 老用户继续对话
"""

import sys
import os
import argparse

# 加载 .env（如果存在）
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory.conversation_manager import ConversationManager, SessionState


def print_separator(char="─", width=60):
    print(char * width)


def print_reply(reply: str):
    """格式化输出系统回复"""
    print_separator()
    print("🤖 助手：")
    print()
    print(reply)
    print()


def print_progress(progress_info: dict):
    """输出进度信息"""
    state = progress_info["state"]
    pct = progress_info["progress"] * 100
    completed = progress_info["completed_groups"]
    pending = progress_info["pending_groups"]

    status_map = {
        SessionState.GREETING: "欢迎阶段",
        SessionState.COLLECTING: "信息收集中",
        SessionState.CONFIRMING: "等待确认",
        SessionState.GENERATING: "报告生成中",
        SessionState.REPORT_DONE: "报告完成",
        SessionState.FOLLOW_UP: "答疑阶段",
    }

    print(f"  📊 状态：{status_map.get(state, state)} | 完成度：{pct:.0f}%")
    if completed:
        print(f"  ✅ 已完成：{' | '.join(completed)}")
    if pending:
        print(f"  ⏳ 待收集：{' | '.join(pending)}")
    print()


def run_chat(user_id: str = None, db_path: str = None):
    """主对话循环"""
    manager = ConversationManager(db_path=db_path)

    # 创建或恢复用户
    if user_id is None:
        user_id = manager.new_user()
        print(f"\n✨ 新用户已创建，user_id: {user_id}")
        print("（下次可用 --user 参数继续对话）\n")
    else:
        if not manager.store.user_exists(user_id):
            print(f"❌ 用户 {user_id} 不存在，创建新用户...")
            user_id = manager.new_user()
        else:
            print(f"\n👋 欢迎回来，user_id: {user_id}")
            profile = manager.store.get_profile(user_id)
            if profile and profile.age:
                print(f"   已有信息：{profile.age}岁 {profile.sex or ''}")
            print()

    # 创建新会话
    session_id = manager.new_session(user_id)

    print_separator("═")
    print("  AI 养老健康助手 - 多轮对话模式")
    print(f"  session_id: {session_id}")
    print_separator("═")
    print("  输入 'q' 或 'quit' 退出")
    print("  输入 'p' 查看当前进度")
    print("  输入 'profile' 查看已收集的用户画像")
    print_separator("═")
    print()

    # 发送初始欢迎消息（空消息触发 GREETING 状态的欢迎语）
    result = manager.chat(session_id, "你好")
    print_reply(result["reply"])

    # 主循环
    while True:
        try:
            # 获取用户输入
            user_input = input("👤 您：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 再见！")
            break

        if not user_input:
            continue

        # 特殊命令
        if user_input.lower() in ("q", "quit", "exit", "退出"):
            print("\n👋 再见！您的信息已保存，下次可以继续对话。")
            print(f"   user_id: {user_id}")
            break

        if user_input.lower() == "p":
            progress = manager.get_progress(session_id)
            print()
            print_progress(progress)
            continue

        if user_input.lower() == "profile":
            profile_dict = manager.get_profile(session_id)
            if profile_dict:
                print()
                print("📋 当前用户画像：")
                for k, v in profile_dict.items():
                    if v is not None and k != "user_type":
                        print(f"   {k}: {v}")
            print()
            continue

        # 发送消息
        result = manager.chat(session_id, user_input)
        print_reply(result["reply"])

        # 显示进度（收集阶段才显示）
        if result["state"] in (SessionState.COLLECTING, SessionState.CONFIRMING):
            progress = manager.get_progress(session_id)
            print_progress(progress)

        # 报告完成后提示
        if result["state"] == SessionState.REPORT_DONE:
            print_separator()
            print("📁 报告已同步保存到 data/output_chat/ 目录")
            print_separator()
            print()


def main():
    parser = argparse.ArgumentParser(
        description="AI 养老健康助手 - 命令行对话模式"
    )
    parser.add_argument(
        "--user", "-u",
        type=str,
        default=None,
        help="已有用户的 user_id（UUID），不填则创建新用户"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLite 数据库路径（默认：data/users.db）"
    )
    args = parser.parse_args()

    run_chat(user_id=args.user, db_path=args.db)


if __name__ == "__main__":
    main()
