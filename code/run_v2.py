#!/usr/bin/env python3
"""
快速运行脚本 - Multi-Agent System V2.0
提供简化的命令行接口
"""

import sys
import os
from multi_agent_system_v2 import (
    load_user_profile_from_excel,
    load_multiple_profiles,
    OrchestratorAgentV2,
    save_results,
    batch_process
)


def run_single_test(row_index=None):
    """运行单个样本测试"""
    print("=" * 80)
    print("单个样本测试模式")
    print("=" * 80)
    
    EXCEL_PATH = '../data/clhls_2018_bilingual_headers-checked.xlsx'
    OUTPUT_DIR = '../data/output_v2_single'
    
    if not os.path.exists(EXCEL_PATH):
        print(f"❌ 数据文件不存在: {EXCEL_PATH}")
        return
    
    try:
        # 如果没有指定行索引，提供快捷选项
        if row_index is None:
            print("\n请选择测试样本：")
            print("1. 状态0（无失能）- 81岁男性 [行10]")
            print("2. 状态1（部分失能）- 100岁男性 [行3]")
            print("3. 状态2（严重失能）- 102岁女性 [行0]")
            print("4. 自定义行号")
            
            choice = input("\n请输入选项 (1/2/3/4，默认为1): ").strip() or "1"
            
            if choice == "1":
                row_index = 10
            elif choice == "2":
                row_index = 3
            elif choice == "3":
                row_index = 0
            elif choice == "4":
                row_input = input("请输入数据行号（0-10294）: ").strip()
                try:
                    row_index = int(row_input)
                    if row_index < 0 or row_index > 10294:
                        print("❌ 行号超出范围")
                        return
                except ValueError:
                    print("❌ 行号必须是整数")
                    return
            else:
                print("❌ 无效的选项")
                return
        
        # 加载指定行的数据
        print(f"\n正在加载数据（行{row_index}）...")
        profile = load_user_profile_from_excel(EXCEL_PATH, row_index=row_index)
        print(f"✅ 成功加载：{profile.age}岁 {profile.sex}")
        
        # 运行评估
        print("\n开始评估...")
        orchestrator = OrchestratorAgentV2()
        results = orchestrator.run(profile, verbose=True)
        
        # 保存结果（传入行号，避免覆盖）
        print("\n保存结果...")
        save_results(results, profile, output_dir=OUTPUT_DIR, row_index=row_index)
        
        print("\n" + "=" * 80)
        print("✅ 测试完成！")
        print(f"结果保存在: {OUTPUT_DIR}")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()


def run_batch_50():
    """运行批量50条数据"""
    print("=" * 80)
    print("批量处理50条数据")
    print("=" * 80)
    
    EXCEL_PATH = '../data/clhls_2018_bilingual_headers-checked.xlsx'
    OUTPUT_DIR = '../data/output_v2_batch50'
    
    if not os.path.exists(EXCEL_PATH):
        print(f"❌ 数据文件不存在: {EXCEL_PATH}")
        return
    
    try:
        # 加载50条数据
        print("\n正在加载数据...")
        profiles = load_multiple_profiles(EXCEL_PATH, n_samples=50, random_state=42)
        
        # 批量处理
        print("\n开始批量处理...")
        all_results = batch_process(
            profiles,
            output_dir=OUTPUT_DIR,
            verbose=False,
            save_reports=True
        )
        
        print("\n" + "=" * 80)
        print("✅ 批量处理完成！")
        print(f"结果保存在: {OUTPUT_DIR}")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ 批量处理失败: {str(e)}")
        import traceback
        traceback.print_exc()


def run_custom(n_samples):
    """运行自定义数量"""
    print("=" * 80)
    print(f"批量处理{n_samples}条数据")
    print("=" * 80)
    
    EXCEL_PATH = '../data/clhls_2018_bilingual_headers-checked.xlsx'
    OUTPUT_DIR = f'../data/output_v2_batch{n_samples}'
    
    if not os.path.exists(EXCEL_PATH):
        print(f"❌ 数据文件不存在: {EXCEL_PATH}")
        return
    
    try:
        # 加载数据
        print("\n正在加载数据...")
        profiles = load_multiple_profiles(EXCEL_PATH, n_samples=n_samples, random_state=42)
        
        # 批量处理
        print("\n开始批量处理...")
        all_results = batch_process(
            profiles,
            output_dir=OUTPUT_DIR,
            verbose=False,
            save_reports=True
        )
        
        print("\n" + "=" * 80)
        print("✅ 批量处理完成！")
        print(f"结果保存在: {OUTPUT_DIR}")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ 批量处理失败: {str(e)}")
        import traceback
        traceback.print_exc()


def show_help():
    """显示帮助信息"""
    print("""
AI 养老健康多 Agent 协作系统 V2.0 - 快速运行脚本

用法:
    python3 run_v2.py [选项] [参数]

选项:
    test [行号]  单个样本测试（快速验证）
                - 不带参数：交互式选择样本
                - 带行号：直接测试指定行的数据
    batch50     批量处理50条数据
    batch N     批量处理N条数据（自定义数量）
    help        显示此帮助信息

示例:
    python3 run_v2.py test          # 交互式选择样本（推荐）
    python3 run_v2.py test 10       # 测试第10行数据（81岁男性）
    python3 run_v2.py test 3        # 测试第3行数据（100岁男性）
    python3 run_v2.py test 0        # 测试第0行数据（102岁女性）
    python3 run_v2.py batch50       # 处理50条数据
    python3 run_v2.py batch 100     # 处理100条数据

常用测试样本:
    行0:  102岁女性 - 状态2（严重失能）
    行3:  100岁男性 - 状态1（部分失能）
    行10: 81岁男性  - 状态0（无失能）

注意:
    - 确保数据文件存在: ../data/clhls_2018_bilingual_headers-checked.xlsx
    - 数据行号范围: 0-10294（共10295条数据）
    - 批量处理时注意API调用频率限制
    - 结果会保存在 ../data/output_v2_* 目录下
""")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("❌ 缺少参数")
        show_help()
        return
    
    command = sys.argv[1].lower()
    
    if command == "test":
        # 检查是否指定了行号
        if len(sys.argv) >= 3:
            try:
                row_index = int(sys.argv[2])
                run_single_test(row_index=row_index)
            except ValueError:
                print("❌ 行号必须是整数")
        else:
            # 交互式选择
            run_single_test()
    
    elif command == "batch50":
        run_batch_50()
    
    elif command == "batch":
        if len(sys.argv) < 3:
            print("❌ 请指定数量，例如: python3 run_v2.py batch 100")
            return
        try:
            n_samples = int(sys.argv[2])
            run_custom(n_samples)
        except ValueError:
            print("❌ 数量必须是整数")
    
    elif command == "help" or command == "-h" or command == "--help":
        show_help()
    
    else:
        print(f"❌ 未知命令: {command}")
        show_help()


if __name__ == "__main__":
    main()

