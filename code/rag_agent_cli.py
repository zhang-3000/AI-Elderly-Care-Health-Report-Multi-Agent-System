#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rag import PageIndexRAGAgent


BACKEND_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INDEX_PATH = BACKEND_ROOT / "data" / "rag_indexes" / "default_index.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PageIndex RAG Agent CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="构建知识索引")
    build_parser.add_argument("sources", nargs="+", help="文档或目录路径，可混合传入")
    build_parser.add_argument(
        "--output",
        default=str(DEFAULT_INDEX_PATH),
        help="索引输出路径",
    )
    build_parser.add_argument(
        "--summary",
        action="store_true",
        help="构建节点摘要（Markdown 可选；PDF 会额外消耗 LLM）",
    )
    build_parser.add_argument("--model", default=None, help="可选模型名")

    query_parser = subparsers.add_parser("query", help="查询知识索引")
    query_parser.add_argument("question", help="查询问题")
    query_parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help="索引文件路径",
    )
    query_parser.add_argument("--top-k", type=int, default=3, help="返回结果数量")
    query_parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    return parser


def run_build(args: argparse.Namespace) -> int:
    agent = PageIndexRAGAgent(model=args.model)
    result = agent.build_index(
        source_paths=args.sources,
        output_path=args.output,
        if_add_node_summary="yes" if args.summary else "no",
    )
    print(f"✅ 索引构建完成: {args.output}")
    print(f"   文档数: {len(result.get('documents', []))}")
    print(f"   Chunk 数: {len(result.get('chunks', []))}")
    return 0


def run_query(args: argparse.Namespace) -> int:
    agent = PageIndexRAGAgent(index_path=args.index)
    payload = agent.build_context(args.question, top_k=args.top_k)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"Query: {payload['query']}")
    print("=" * 80)
    if not payload["hits"]:
        print("未命中任何结果")
        return 0

    for idx, hit in enumerate(payload["hits"], start=1):
        print(f"[{idx}] {hit.get('doc_name')} / {hit.get('path')}")
        if hit.get("line_num") is not None:
            print(f"    line: {hit['line_num']}")
        if hit.get("start_index") is not None:
            print(f"    page: {hit['start_index']}")
        print(f"    score: {hit['score']}")
        print(f"    excerpt: {hit.get('excerpt', '')}")
        print()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "build":
        return run_build(args)
    if args.command == "query":
        return run_query(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
