#!/usr/bin/env python3
"""
一键生成报告并评测。

流程：加载数据 → RAG 检索 → 多 Agent 生成报告 → 三指标自动评测

用法:
    # 测试预置样本（默认行 10，81 岁男性）
    python run_and_evaluate.py

    # 指定数据行
    python run_and_evaluate.py --row 3

    # 指定 RAG 索引
    python run_and_evaluate.py --row 10 --index ../data/rag_indexes/guidelines_all_index.json

    # 只运行部分评测指标
    python run_and_evaluate.py --row 10 --metrics coverage

    # 跳过评测，只生成报告
    python run_and_evaluate.py --row 10 --skip-eval

    # 批量：多行数据
    python run_and_evaluate.py --rows 0,3,10
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 code 目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── 默认路径 ──────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DEFAULT_EXCEL = DATA_DIR / "clhls_2018_bilingual_headers-checked.xlsx"
DEFAULT_INDEX = DATA_DIR / "rag_indexes" / "guidelines_all_index.json"
DEFAULT_OUTPUT = DATA_DIR / "output_eval"

# 预置样本
PRESET_ROWS = {
    0: "102岁女性（严重失能）",
    3: "100岁男性（部分失能）",
    10: "81岁男性（无失能）",
}


def generate_report(
    excel_path: Path,
    row_index: int,
    rag_index: Path,
    output_dir: Path,
    rag_top_k: int = 5,
) -> Optional[Dict[str, Any]]:
    """
    生成一份报告，返回 {json_path, report_path, results, profile}。
    """
    # 设置 RAG 环境变量（在 import 之前）
    os.environ["RAG_ENABLED"] = "true"
    os.environ["RAG_INDEX_PATH"] = str(rag_index)
    os.environ["RAG_TOP_K"] = str(rag_top_k)

    from multi_agent_system_v2 import (
        OrchestratorAgentV2,
        load_user_profile_from_excel,
        save_results,
    )

    profile = load_user_profile_from_excel(str(excel_path), row_index=row_index)
    logger.info("加载画像: %s岁 %s (行%d)", profile.age, profile.sex, row_index)

    orchestrator = OrchestratorAgentV2()
    results = orchestrator.run(profile, verbose=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, report_path = save_results(
        results, profile, output_dir=str(output_dir), row_index=row_index
    )

    return {
        "json_path": json_path,
        "report_path": report_path,
        "results": results,
        "profile": asdict(profile),
    }


def evaluate_report(
    json_path: str,
    metrics: set,
) -> Dict[str, Any]:
    """
    评测一份已生成的报告 JSON，返回评测结果字典。
    """
    from evaluation.evaluator import ReportEvaluator

    evaluator = ReportEvaluator()
    result = evaluator.evaluate_from_file(json_path)

    if "faithfulness" not in metrics:
        result.faithfulness = None
    if "coverage" not in metrics:
        result.profile_coverage = None
    if "context_relevance" not in metrics:
        result.context_relevance = None

    return result


def print_eval_results(result, json_path: str) -> None:
    """打印评测结果并保存。"""
    summary = result.summary()

    print(f"\n{'─'*60}")
    print("📊 评测结果")
    print(f"{'─'*60}")
    for name, score in summary.items():
        bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
        print(f"  {name:20s}: {score:.4f} [{bar}]")

    if result.faithfulness:
        unsupported = [
            s for s in result.faithfulness.statements if not s["supported"]
        ]
        print(f"\n  Faithfulness: {result.faithfulness.supported_statements}/{result.faithfulness.total_statements} 条陈述有上下文支持")
        if unsupported:
            print(f"  未支持的陈述（前5条）:")
            for s in unsupported[:5]:
                print(f"    ✗ {s['statement'][:70]}")

    if result.profile_coverage:
        uncovered = [e for e in result.profile_coverage.elements if not e["covered"]]
        print(f"\n  Profile Coverage: {result.profile_coverage.covered_elements}/{result.profile_coverage.total_elements} 个要素被覆盖")
        if uncovered:
            print(f"  未覆盖: {', '.join(e['element'] for e in uncovered)}")

    if result.context_relevance:
        print(f"\n  Context Relevance: {result.context_relevance.useful_sentences}/{result.context_relevance.total_sentences} 个句子有用")

    # 保存
    eval_path = Path(json_path).parent / f"eval_{Path(json_path).stem}.json"
    eval_data = {
        "source": str(json_path),
        "evaluated_at": datetime.now().isoformat(),
        **result.to_dict(),
    }
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    print(f"\n  评测结果已保存: {eval_path}")


def run_pipeline(
    row_index: int,
    excel_path: Path,
    rag_index: Path,
    output_dir: Path,
    metrics: set,
    skip_eval: bool,
    rag_top_k: int,
) -> Optional[Dict[str, Any]]:
    """运行完整流程：生成 + 评测。"""
    preset_label = PRESET_ROWS.get(row_index, "")
    header = f"行{row_index}"
    if preset_label:
        header += f" ({preset_label})"

    print(f"\n{'='*60}")
    print(f"  流程开始: {header}")
    print(f"{'='*60}")

    t0 = time.time()

    # ── 阶段一：生成报告 ──
    print(f"\n📝 阶段一：生成报告")
    print(f"  数据源: {excel_path.name} 行{row_index}")
    print(f"  RAG 索引: {rag_index.name}")
    print(f"  RAG Top-K: {rag_top_k}")

    gen = generate_report(excel_path, row_index, rag_index, output_dir, rag_top_k)
    if gen is None:
        logger.error("报告生成失败")
        return None

    t_gen = time.time() - t0
    knowledge = gen["results"].get("knowledge", {})
    total_hits = knowledge.get("total_hits", 0)
    print(f"\n  ✅ 报告已生成 ({t_gen:.1f}s)")
    print(f"     RAG 命中: {total_hits} 条")
    print(f"     JSON: {gen['json_path']}")
    print(f"     报告: {gen['report_path']}")

    if skip_eval:
        print(f"\n  ⏭️  跳过评测（--skip-eval）")
        return {"generation": gen, "evaluation": None}

    # ── 阶段二：评测 ──
    print(f"\n🔍 阶段二：评测报告")
    print(f"  指标: {', '.join(sorted(metrics))}")

    t1 = time.time()
    result = evaluate_report(gen["json_path"], metrics)
    t_eval = time.time() - t1

    print_eval_results(result, gen["json_path"])

    t_total = time.time() - t0
    print(f"\n⏱️  耗时: 生成 {t_gen:.1f}s + 评测 {t_eval:.1f}s = 总计 {t_total:.1f}s")

    return {"generation": gen, "evaluation": result.to_dict()}


def main():
    parser = argparse.ArgumentParser(
        description="一键生成报告并评测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""预置样本:
  行0:  102岁女性（严重失能）
  行3:  100岁男性（部分失能）
  行10: 81岁男性（无失能）

示例:
  python {Path(__file__).name}                          # 默认行10
  python {Path(__file__).name} --row 3                  # 指定行
  python {Path(__file__).name} --rows 0,3,10            # 批量
  python {Path(__file__).name} --metrics coverage       # 只跑覆盖度
  python {Path(__file__).name} --skip-eval              # 只生成不评测""",
    )
    parser.add_argument(
        "--row", type=int, default=None,
        help="数据行号（默认 10）",
    )
    parser.add_argument(
        "--rows", type=str, default=None,
        help="多行号，逗号分隔（如 0,3,10）",
    )
    parser.add_argument(
        "--excel", type=str, default=str(DEFAULT_EXCEL),
        help=f"Excel 数据文件（默认: {DEFAULT_EXCEL.name}）",
    )
    parser.add_argument(
        "--index", type=str, default=str(DEFAULT_INDEX),
        help=f"RAG 索引文件（默认: {DEFAULT_INDEX.name}）",
    )
    parser.add_argument(
        "--output", "-o", type=str, default=str(DEFAULT_OUTPUT),
        help=f"输出目录（默认: {DEFAULT_OUTPUT.name}）",
    )
    parser.add_argument(
        "--top-k", type=int, default=5,
        help="RAG 检索 Top-K（默认: 5）",
    )
    parser.add_argument(
        "--metrics", "-m", type=str,
        default="faithfulness,coverage,context_relevance",
        help="评测指标，逗号分隔（默认: faithfulness,coverage,context_relevance）",
    )
    parser.add_argument(
        "--skip-eval", action="store_true",
        help="跳过评测，只生成报告",
    )

    args = parser.parse_args()

    excel_path = Path(args.excel)
    rag_index = Path(args.index)
    output_dir = Path(args.output)
    metrics = {m.strip() for m in args.metrics.split(",")}

    if not excel_path.exists():
        print(f"❌ 数据文件不存在: {excel_path}")
        sys.exit(1)
    if not rag_index.exists():
        print(f"❌ RAG 索引不存在: {rag_index}")
        sys.exit(1)

    # 确定要处理的行
    if args.rows:
        row_indices = [int(r.strip()) for r in args.rows.split(",")]
    elif args.row is not None:
        row_indices = [args.row]
    else:
        row_indices = [10]

    print(f"{'='*60}")
    print(f"  AI 养老健康报告 —— 生成 & 评测流水线")
    print(f"{'='*60}")
    print(f"  待处理: {len(row_indices)} 份报告 (行 {','.join(map(str, row_indices))})")
    print(f"  RAG 索引: {rag_index.name}")
    print(f"  输出目录: {output_dir}")

    all_summaries: List[Dict[str, Any]] = []
    for row_index in row_indices:
        result = run_pipeline(
            row_index=row_index,
            excel_path=excel_path,
            rag_index=rag_index,
            output_dir=output_dir,
            metrics=metrics,
            skip_eval=args.skip_eval,
            rag_top_k=args.top_k,
        )
        if result and result.get("evaluation"):
            summary = result["evaluation"].get("summary", {})
            summary["row"] = row_index
            all_summaries.append(summary)

    # 汇总
    if len(all_summaries) > 1:
        print(f"\n{'='*60}")
        print(f"  汇总（共 {len(all_summaries)} 份报告）")
        print(f"{'='*60}")
        for metric in ["faithfulness", "profile_coverage", "context_relevance"]:
            scores = [s[metric] for s in all_summaries if metric in s]
            if scores:
                avg = sum(scores) / len(scores)
                print(f"  {metric:20s}: avg={avg:.4f}  min={min(scores):.4f}  max={max(scores):.4f}")

    print(f"\n{'='*60}")
    print(f"  ✅ 全部完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
