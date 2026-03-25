#!/usr/bin/env python3
"""
按批次并发运行报告生成与评测，并输出汇总分析。

默认行为：
- 处理行 1-20
- 每批 5 个并发 worker
- 共 4 批
- 每个 worker 调用现有 run_and_evaluate.py 子进程
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
DATA_DIR = BACKEND_ROOT / "data"
DEFAULT_EXCEL = DATA_DIR / "clhls_2018_bilingual_headers-checked.xlsx"
DEFAULT_INDEX = DATA_DIR / "rag_indexes" / "guidelines_all_index.json"
DEFAULT_OUTPUT_ROOT = DATA_DIR / "output_eval_v2_parallel20"
DEFAULT_ROWS = list(range(1, 21))
DEFAULT_PARALLEL = 5
DEFAULT_TOP_K = 5
DEFAULT_METRICS = (
    "input_grounding,guideline_grounding,profile_coverage,"
    "doc_routing_relevance,node_evidence_relevance,evidence_coverage"
)


@dataclass
class RowRunResult:
    row: int
    batch_id: int
    returncode: int
    duration_seconds: float
    log_path: str
    result_json: str | None
    report_md: str | None
    eval_json: str | None
    error: str | None = None


def chunked(items: Sequence[int], size: int) -> List[List[int]]:
    if size <= 0:
        raise ValueError("batch size must be > 0")
    return [list(items[idx: idx + size]) for idx in range(0, len(items), size)]


def latest_match(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime)
    return matches[-1] if matches else None


def parse_rows(text: str | None) -> List[int]:
    if not text:
        return list(DEFAULT_ROWS)
    rows: List[int] = []
    for part in text.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text.strip())
            end = int(end_text.strip())
            step = 1 if end >= start else -1
            rows.extend(range(start, end + step, step))
        else:
            rows.append(int(item))
    deduped: List[int] = []
    seen = set()
    for row in rows:
        if row in seen:
            continue
        seen.add(row)
        deduped.append(row)
    return deduped


def build_command(
    python_bin: Path,
    row: int,
    excel_path: Path,
    rag_index: Path,
    output_dir: Path,
    metrics: str,
    top_k: int,
) -> List[str]:
    return [
        str(python_bin),
        str(SCRIPT_DIR / "run_and_evaluate.py"),
        "--row",
        str(row),
        "--excel",
        str(excel_path),
        "--index",
        str(rag_index),
        "--output",
        str(output_dir),
        "--top-k",
        str(top_k),
        "--metrics",
        metrics,
    ]


def run_batch(
    batch_rows: Sequence[int],
    batch_id: int,
    python_bin: Path,
    excel_path: Path,
    rag_index: Path,
    output_dir: Path,
    logs_dir: Path,
    metrics: str,
    top_k: int,
) -> List[RowRunResult]:
    active = []
    for row in batch_rows:
        log_path = logs_dir / f"batch_{batch_id:02d}_row_{row:04d}.log"
        cmd = build_command(
            python_bin=python_bin,
            row=row,
            excel_path=excel_path,
            rag_index=rag_index,
            output_dir=output_dir,
            metrics=metrics,
            top_k=top_k,
        )
        log_file = open(log_path, "w", encoding="utf-8")
        started_at = time.time()
        process = subprocess.Popen(
            cmd,
            cwd=str(BACKEND_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        active.append(
            {
                "row": row,
                "batch_id": batch_id,
                "process": process,
                "log_path": log_path,
                "log_file": log_file,
                "started_at": started_at,
            }
        )

    results: List[RowRunResult] = []
    for item in active:
        process = item["process"]
        returncode = process.wait()
        item["log_file"].close()
        duration = time.time() - item["started_at"]
        row = item["row"]

        result_json = latest_match(output_dir, f"result_row{row}_*.json")
        report_md = latest_match(output_dir, f"report_row{row}_*.md")
        eval_json = latest_match(output_dir, f"eval_result_row{row}_*.json")
        error = None
        if returncode != 0:
            error = f"subprocess exited with code {returncode}"
        elif eval_json is None:
            error = "evaluation result file not found"

        results.append(
            RowRunResult(
                row=row,
                batch_id=batch_id,
                returncode=returncode,
                duration_seconds=round(duration, 2),
                log_path=str(item["log_path"]),
                result_json=str(result_json) if result_json else None,
                report_md=str(report_md) if report_md else None,
                eval_json=str(eval_json) if eval_json else None,
                error=error,
            )
        )
    return sorted(results, key=lambda item: item.row)


def safe_mean(values: Iterable[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    return sum(values) / len(values)


def analyze_outputs(output_dir: Path, run_results: Sequence[RowRunResult]) -> Dict[str, Any]:
    metric_names = [
        "input_grounding",
        "guideline_grounding",
        "profile_coverage",
        "doc_routing_relevance",
        "node_evidence_relevance",
        "evidence_coverage",
    ]

    eval_rows = []
    failures = []
    uncovered_counter: Counter[str] = Counter()
    unsupported_input_counter: Counter[str] = Counter()
    unsupported_guideline_counter: Counter[str] = Counter()
    retrieval_mode_counter: Counter[str] = Counter()

    for item in run_results:
        if item.eval_json is None:
            failures.append({"row": item.row, "error": item.error, "log_path": item.log_path})
            continue

        payload = json.loads(Path(item.eval_json).read_text(encoding="utf-8"))
        result_payload = {}
        if item.result_json:
            result_payload = json.loads(Path(item.result_json).read_text(encoding="utf-8"))

        summary = payload.get("summary", {})
        metadata = payload.get("metadata", {})
        retrieval_mode = metadata.get("retrieval_mode") or result_payload.get("knowledge", {}).get("retrieval_mode")
        if retrieval_mode:
            retrieval_mode_counter[retrieval_mode] += 1

        profile_coverage = payload.get("profile_coverage") or {}
        for element in profile_coverage.get("elements", []):
            if not element.get("covered"):
                uncovered_counter[str(element.get("element") or "").strip()] += 1

        input_grounding = payload.get("input_grounding") or {}
        for statement in input_grounding.get("statements", []):
            if not statement.get("supported"):
                unsupported_input_counter[str(statement.get("statement") or "").strip()] += 1

        guideline_grounding = payload.get("guideline_grounding") or {}
        for statement in guideline_grounding.get("statements", []):
            if not statement.get("supported"):
                unsupported_guideline_counter[str(statement.get("statement") or "").strip()] += 1

        composite_values = [summary[name] for name in metric_names if name in summary]
        composite_score = safe_mean(composite_values)

        eval_rows.append(
            {
                "row": item.row,
                "summary": summary,
                "metadata": metadata,
                "retrieval_mode": retrieval_mode,
                "composite_score": round(composite_score, 4) if composite_score is not None else None,
                "duration_seconds": item.duration_seconds,
                "result_json": item.result_json,
                "report_md": item.report_md,
                "eval_json": item.eval_json,
                "log_path": item.log_path,
            }
        )

    metric_summary: Dict[str, Any] = {}
    for metric in metric_names:
        scored_rows = [(row["row"], row["summary"].get(metric)) for row in eval_rows if metric in row["summary"]]
        values = [score for _, score in scored_rows if score is not None]
        if not values:
            continue
        min_row, min_score = min(scored_rows, key=lambda item: item[1])
        max_row, max_score = max(scored_rows, key=lambda item: item[1])
        metric_summary[metric] = {
            "count": len(values),
            "average": round(sum(values) / len(values), 4),
            "median": round(statistics.median(values), 4),
            "min": round(min_score, 4),
            "min_row": min_row,
            "max": round(max_score, 4),
            "max_row": max_row,
        }

    composite_rows = [row for row in eval_rows if row["composite_score"] is not None]
    worst_rows = sorted(composite_rows, key=lambda item: item["composite_score"])[:5]
    best_rows = sorted(composite_rows, key=lambda item: item["composite_score"], reverse=True)[:5]

    return {
        "generated_at": datetime.now().isoformat(),
        "output_dir": str(output_dir),
        "requested_rows": [item.row for item in run_results],
        "completed_rows": [row["row"] for row in eval_rows],
        "failure_count": len(failures),
        "failures": failures,
        "retrieval_modes": dict(retrieval_mode_counter),
        "metric_summary": metric_summary,
        "worst_rows": worst_rows,
        "best_rows": best_rows,
        "top_uncovered_profile_elements": uncovered_counter.most_common(10),
        "top_unsupported_input_statements": unsupported_input_counter.most_common(10),
        "top_unsupported_guideline_statements": unsupported_guideline_counter.most_common(10),
        "rows": eval_rows,
    }


def render_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# 20 份报告并发评测汇总",
        "",
        f"- 生成时间: `{summary['generated_at']}`",
        f"- 输出目录: `{summary['output_dir']}`",
        f"- 完成数量: `{len(summary['completed_rows'])}/{len(summary['requested_rows'])}`",
        f"- 失败数量: `{summary['failure_count']}`",
        f"- 检索模式分布: `{summary['retrieval_modes']}`",
        "",
        "## 指标汇总",
        "",
        "| 指标 | 平均 | 中位数 | 最低 (行) | 最高 (行) |",
        "| --- | ---: | ---: | --- | --- |",
    ]

    for metric, stats in summary["metric_summary"].items():
        lines.append(
            f"| {metric} | {stats['average']:.4f} | {stats['median']:.4f} | "
            f"{stats['min']:.4f} (row {stats['min_row']}) | "
            f"{stats['max']:.4f} (row {stats['max_row']}) |"
        )

    lines.extend(
        [
            "",
            "## 综合得分最低的 5 行",
            "",
            "| 行号 | 综合均分 | 检索模式 | 耗时(s) |",
            "| --- | ---: | --- | ---: |",
        ]
    )
    for row in summary["worst_rows"]:
        lines.append(
            f"| {row['row']} | {row['composite_score']:.4f} | {row['retrieval_mode']} | {row['duration_seconds']:.1f} |"
        )

    lines.extend(
        [
            "",
            "## 综合得分最高的 5 行",
            "",
            "| 行号 | 综合均分 | 检索模式 | 耗时(s) |",
            "| --- | ---: | --- | ---: |",
        ]
    )
    for row in summary["best_rows"]:
        lines.append(
            f"| {row['row']} | {row['composite_score']:.4f} | {row['retrieval_mode']} | {row['duration_seconds']:.1f} |"
        )

    lines.extend(["", "## 常见未覆盖画像要素", ""])
    for element, count in summary["top_uncovered_profile_elements"]:
        lines.append(f"- `{element}`: {count} 次")

    lines.extend(["", "## 常见未被输入证据支持的陈述", ""])
    for statement, count in summary["top_unsupported_input_statements"][:5]:
        lines.append(f"- `{count}` 次: {statement}")

    lines.extend(["", "## 常见未被知识证据支持的陈述", ""])
    for statement, count in summary["top_unsupported_guideline_statements"][:5]:
        lines.append(f"- `{count}` 次: {statement}")

    if summary["failures"]:
        lines.extend(["", "## 失败记录", ""])
        for item in summary["failures"]:
            lines.append(f"- row {item['row']}: {item['error']} ({item['log_path']})")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="并发批量生成报告并评测")
    parser.add_argument("--rows", type=str, default="1-20", help="行号列表或范围，如 1-20 或 1,3,10")
    parser.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL, help="并发 worker 数")
    parser.add_argument("--excel", type=str, default=str(DEFAULT_EXCEL), help="Excel 路径")
    parser.add_argument("--index", type=str, default=str(DEFAULT_INDEX), help="RAG 索引路径")
    parser.add_argument("--output-root", type=str, default=str(DEFAULT_OUTPUT_ROOT), help="输出根目录")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="RAG top-k")
    parser.add_argument("--metrics", type=str, default=DEFAULT_METRICS, help="评测指标列表")
    args = parser.parse_args()

    rows = parse_rows(args.rows)
    excel_path = Path(args.excel).resolve()
    rag_index = Path(args.index).resolve()
    output_root = Path(args.output_root).resolve()
    python_bin = Path(sys.executable).resolve()

    if not excel_path.exists():
        raise FileNotFoundError(f"excel not found: {excel_path}")
    if not rag_index.exists():
        raise FileNotFoundError(f"rag index not found: {rag_index}")
    if args.parallel <= 0:
        raise ValueError("parallel must be > 0")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"run_{timestamp}"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    batches = chunked(rows, args.parallel)
    manifest: Dict[str, Any] = {
        "created_at": datetime.now().isoformat(),
        "rows": rows,
        "parallel": args.parallel,
        "batch_count": len(batches),
        "excel_path": str(excel_path),
        "rag_index": str(rag_index),
        "run_dir": str(run_dir),
        "batches": [],
    }

    print(f"并发批跑开始: rows={rows} parallel={args.parallel} batches={len(batches)}")
    print(f"输出目录: {run_dir}")

    all_results: List[RowRunResult] = []
    overall_started_at = time.time()
    for batch_index, batch_rows in enumerate(batches, start=1):
        batch_started_at = time.time()
        print(f"\n=== Batch {batch_index}/{len(batches)} rows={batch_rows} ===")
        batch_results = run_batch(
            batch_rows=batch_rows,
            batch_id=batch_index,
            python_bin=python_bin,
            excel_path=excel_path,
            rag_index=rag_index,
            output_dir=run_dir,
            logs_dir=logs_dir,
            metrics=args.metrics,
            top_k=args.top_k,
        )
        batch_duration = round(time.time() - batch_started_at, 2)
        all_results.extend(batch_results)
        manifest["batches"].append(
            {
                "batch_id": batch_index,
                "rows": list(batch_rows),
                "duration_seconds": batch_duration,
                "results": [item.__dict__ for item in batch_results],
            }
        )
        with open(run_dir / "run_manifest.json", "w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
        success_count = sum(1 for item in batch_results if item.error is None)
        print(f"Batch {batch_index} 完成: success={success_count}/{len(batch_results)} duration={batch_duration:.1f}s")

    summary = analyze_outputs(run_dir, all_results)
    summary["total_duration_seconds"] = round(time.time() - overall_started_at, 2)
    with open(run_dir / "summary.json", "w", encoding="utf-8") as file:
        json.dump(summary, file, ensure_ascii=False, indent=2)
    with open(run_dir / "summary.md", "w", encoding="utf-8") as file:
        file.write(render_markdown(summary))

    print("\n=== 全部批次完成 ===")
    print(f"完成数量: {len(summary['completed_rows'])}/{len(summary['requested_rows'])}")
    print(f"失败数量: {summary['failure_count']}")
    print(f"总耗时: {summary['total_duration_seconds']:.1f}s")
    print(f"汇总文件: {run_dir / 'summary.md'}")
    return 0 if summary["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
