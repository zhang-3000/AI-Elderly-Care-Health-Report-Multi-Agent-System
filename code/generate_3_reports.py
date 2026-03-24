#!/usr/bin/env python3
"""
生成3份不同失能程度的报告
状态0（无失能）、状态1（部分失能）、状态2（严重失能）
"""

import os
import sys
from multi_agent_system_v2 import (
    load_user_profile_from_excel,
    OrchestratorAgentV2,
    save_results
)

def main():
    print("=" * 80)
    print("生成3份不同失能程度的健康评估与照护行动计划")
    print("=" * 80)
    
    # 数据文件路径
    EXCEL_PATH = '../data/clhls_2018_bilingual_headers-checked.xlsx'
    OUTPUT_DIR = '../result'
    
    # 根据前面的分析，选择3个样本的索引
    # 状态0: 行10 (81岁男性)
    # 状态1: 行3 (100岁男性)
    # 状态2: 行0 (102岁女性)
    samples = [
        {'index': 10, 'status': 0, 'desc': '无失能'},
        {'index': 3, 'status': 1, 'desc': '部分失能'},
        {'index': 0, 'status': 2, 'desc': '严重失能'}
    ]
    
    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 创建调度中心
    orchestrator = OrchestratorAgentV2()
    
    # 处理每个样本
    for i, sample in enumerate(samples):
        print(f"\n{'='*80}")
        print(f"处理样本 {i+1}/3: 状态{sample['status']}（{sample['desc']}）")
        print(f"数据行索引: {sample['index']}")
        print(f"{'='*80}\n")
        
        try:
            # 加载用户数据
            print(f"正在加载数据...")
            profile = load_user_profile_from_excel(EXCEL_PATH, row_index=sample['index'])
            print(f"✅ 成功加载：{profile.age}岁 {profile.sex}")
            
            # 运行评估
            print(f"\n开始评估...")
            results = orchestrator.run(profile, verbose=True)
            
            # 保存结果（使用特定的文件名）
            print(f"\n保存结果...")
            
            # 自定义保存路径
            import json
            from datetime import datetime
            from dataclasses import asdict
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            status_name = sample['desc']
            user_id = f"status{sample['status']}_{profile.age}_{profile.sex}"
            
            # 保存JSON
            json_path = os.path.join(OUTPUT_DIR, f"result_{user_id}.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                results_copy = results.copy()
                results_copy['profile'] = asdict(profile)
                results_copy['sample_info'] = sample
                json.dump(results_copy, f, ensure_ascii=False, indent=2)
            
            # 保存Markdown报告
            report_path = os.path.join(OUTPUT_DIR, f"report_{user_id}.md")
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(f"# 样本信息\n\n")
                f.write(f"- **失能状态**: 状态{sample['status']}（{sample['desc']}）\n")
                f.write(f"- **年龄**: {profile.age}岁\n")
                f.write(f"- **性别**: {profile.sex}\n")
                f.write(f"- **数据行**: 第{sample['index']}行\n\n")
                f.write(f"---\n\n")
                f.write(results['report'])
            
            print(f"✅ 报告已保存:")
            print(f"   - JSON: {json_path}")
            print(f"   - 报告: {report_path}")
            
            print(f"\n✅ 样本 {i+1}/3 处理完成！")
            
        except Exception as e:
            print(f"❌ 样本 {i+1}/3 处理失败: {str(e)}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"\n{'='*80}")
    print("✅ 所有报告生成完成！")
    print(f"报告保存在: {OUTPUT_DIR}")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
