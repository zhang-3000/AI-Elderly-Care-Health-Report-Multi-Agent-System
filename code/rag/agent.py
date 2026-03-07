from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .page_index import page_index
from .page_index_md import md_to_tree
from .utils import DEFAULT_CHAT_MODEL


SUPPORTED_SOURCE_TYPES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",
}


def _unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _normalize_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _tokenize_query(query: str) -> List[str]:
    normalized = _normalize_text(query)
    if not normalized:
        return []

    tokens: List[str] = []
    for item in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]+", normalized):
        tokens.append(item)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", item):
            tokens.extend(item[idx: idx + 2] for idx in range(len(item) - 1))
    return _unique_keep_order(tokens)


class PageIndexRAGAgent:
    """基于 PageIndex 结构树的轻量检索 Agent。"""

    def __init__(self, index_path: Optional[str] = None, model: Optional[str] = None):
        self.model = model or DEFAULT_CHAT_MODEL
        self.index_path = str(Path(index_path).resolve()) if index_path else None
        self.index_data: Optional[Dict[str, Any]] = None

        if self.index_path and Path(self.index_path).exists():
            self.index_data = self.load_index(self.index_path)

    def build_index(
        self,
        source_paths: Sequence[str] | str,
        output_path: str,
        if_add_node_summary: str = "no",
        summary_token_threshold: int = 200,
    ) -> Dict[str, Any]:
        sources = self._collect_source_paths(source_paths)
        if not sources:
            raise ValueError("未找到可索引的 Markdown/PDF 文档")

        documents: List[Dict[str, Any]] = []
        chunks: List[Dict[str, Any]] = []

        for source_path in sources:
            doc_result = self._build_document_result(
                source_path=source_path,
                if_add_node_summary=if_add_node_summary,
                summary_token_threshold=summary_token_threshold,
            )
            documents.append(doc_result)
            chunks.extend(
                self._flatten_structure(
                    structure=doc_result.get("structure", []),
                    doc_name=doc_result.get("doc_name") or source_path.stem,
                    source_path=str(source_path.resolve()),
                    source_type=doc_result.get("source_type", "unknown"),
                )
            )

        index_data = {
            "built_at": datetime.now().isoformat(),
            "model": self.model,
            "documents": documents,
            "chunks": chunks,
        }

        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as file:
            json.dump(index_data, file, ensure_ascii=False, indent=2)

        self.index_path = str(output)
        self.index_data = index_data
        return index_data

    def load_index(self, index_path: Optional[str] = None) -> Dict[str, Any]:
        target_path = Path(index_path or self.index_path or "").resolve()
        if not target_path.exists():
            raise FileNotFoundError(f"索引文件不存在: {target_path}")
        with open(target_path, "r", encoding="utf-8") as file:
            data = json.load(file)
        self.index_path = str(target_path)
        self.index_data = data
        return data

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        index_data = self._require_index()
        tokens = _tokenize_query(query)
        normalized_query = _normalize_text(query)

        hits: List[Dict[str, Any]] = []
        for chunk in index_data.get("chunks", []):
            score, matched_terms = self._score_chunk(chunk, tokens, normalized_query)
            if score <= 0:
                continue

            excerpt = self._build_excerpt(chunk, matched_terms)
            hits.append(
                {
                    "score": score,
                    "doc_name": chunk.get("doc_name"),
                    "source_path": chunk.get("source_path"),
                    "source_type": chunk.get("source_type"),
                    "title": chunk.get("title"),
                    "path": chunk.get("path"),
                    "node_id": chunk.get("node_id"),
                    "start_index": chunk.get("start_index"),
                    "end_index": chunk.get("end_index"),
                    "line_num": chunk.get("line_num"),
                    "matched_terms": matched_terms,
                    "excerpt": excerpt,
                }
            )

        hits.sort(key=lambda item: (-item["score"], item.get("doc_name") or "", item.get("path") or ""))
        return hits[:top_k]

    def build_context(self, query: str, top_k: int = 3) -> Dict[str, Any]:
        hits = self.retrieve(query, top_k=top_k)
        context_parts = []
        for idx, hit in enumerate(hits, start=1):
            location = []
            if hit.get("line_num") is not None:
                location.append(f"line={hit['line_num']}")
            if hit.get("start_index") is not None:
                location.append(f"page={hit['start_index']}")
            location_text = f" ({', '.join(location)})" if location else ""
            context_parts.append(
                f"[参考{idx}] {hit.get('doc_name', '未知文档')} / {hit.get('path', hit.get('title', '未命名节点'))}{location_text}\n"
                f"{hit.get('excerpt', '')}"
            )

        return {
            "query": query,
            "hits": hits,
            "context": "\n\n".join(context_parts),
        }

    def retrieve_for_profile(
        self,
        profile: Any,
        status_result: Dict[str, Any],
        risk_result: Dict[str, Any],
        factor_result: Dict[str, Any],
        top_k: int = 3,
    ) -> Dict[str, Any]:
        query = self.build_profile_query(profile, status_result, risk_result, factor_result)
        payload = self.build_context(query, top_k=top_k)
        payload["enabled"] = True
        return payload

    def build_profile_query(
        self,
        profile: Any,
        status_result: Dict[str, Any],
        risk_result: Dict[str, Any],
        factor_result: Dict[str, Any],
    ) -> str:
        chronic_fields = {
            "hypertension": "高血压",
            "diabetes": "糖尿病",
            "heart_disease": "心脏病",
            "stroke": "中风",
            "arthritis": "关节炎",
            "cancer": "肿瘤",
        }

        active_conditions = [
            label
            for field, label in chronic_fields.items()
            if str(getattr(profile, field, "") or "").strip() in {"是", "有", "患有", "1", "true", "True"}
        ]

        risk_names = [
            item.get("risk", "")
            for item in (risk_result.get("short_term_risks", []) + risk_result.get("medium_term_risks", []))
        ]

        factor_keywords = []
        for item in factor_result.get("main_problems", []) or []:
            if isinstance(item, str):
                factor_keywords.append(item)
            elif isinstance(item, dict):
                factor_keywords.extend(
                    [item.get("problem", ""), item.get("impact", "")]
                )

        query_parts = _unique_keep_order(
            [
                f"{getattr(profile, 'age', '')}岁老人",
                str(getattr(profile, "sex", "") or ""),
                status_result.get("status_name", ""),
                risk_result.get("overall_risk_level", ""),
                *risk_names[:4],
                *factor_keywords[:4],
                *active_conditions,
                "老年照护",
                "居家照护",
            ]
        )
        return " ".join([part for part in query_parts if str(part).strip()])

    def _require_index(self) -> Dict[str, Any]:
        if self.index_data is None:
            if not self.index_path:
                raise ValueError("RAG 索引尚未加载，请先 build_index 或 load_index")
            self.index_data = self.load_index(self.index_path)
        return self.index_data

    def _collect_source_paths(self, source_paths: Sequence[str] | str) -> List[Path]:
        if isinstance(source_paths, (str, os.PathLike)):
            raw_inputs = [source_paths]
        else:
            raw_inputs = list(source_paths)

        collected: List[Path] = []
        for raw in raw_inputs:
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                continue
            if path.is_dir():
                for suffix in SUPPORTED_SOURCE_TYPES:
                    collected.extend(sorted(path.rglob(f"*{suffix}")))
            elif path.suffix.lower() in SUPPORTED_SOURCE_TYPES:
                collected.append(path)

        unique_paths = []
        seen = set()
        for path in collected:
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            unique_paths.append(path)
        return unique_paths

    def _build_document_result(
        self,
        source_path: Path,
        if_add_node_summary: str,
        summary_token_threshold: int,
    ) -> Dict[str, Any]:
        source_type = SUPPORTED_SOURCE_TYPES.get(source_path.suffix.lower())
        if source_type == "markdown":
            result = asyncio.run(
                md_to_tree(
                    md_path=str(source_path),
                    if_thinning=False,
                    min_token_threshold=None,
                    if_add_node_summary=if_add_node_summary,
                    summary_token_threshold=summary_token_threshold,
                    model=self.model,
                    if_add_doc_description="no",
                    if_add_node_text="yes",
                    if_add_node_id="yes",
                )
            )
        elif source_type == "pdf":
            result = page_index(
                str(source_path),
                model=self.model,
                if_add_node_id="yes",
                if_add_node_summary=if_add_node_summary,
                if_add_doc_description="no",
                if_add_node_text="yes",
            )
        else:
            raise ValueError(f"不支持的文档类型: {source_path}")

        result["source_path"] = str(source_path.resolve())
        result["source_type"] = source_type
        return result

    def _flatten_structure(
        self,
        structure: Any,
        doc_name: str,
        source_path: str,
        source_type: str,
        parents: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if parents is None:
            parents = []

        chunks: List[Dict[str, Any]] = []

        if isinstance(structure, list):
            for node in structure:
                chunks.extend(
                    self._flatten_structure(
                        structure=node,
                        doc_name=doc_name,
                        source_path=source_path,
                        source_type=source_type,
                        parents=parents,
                    )
                )
            return chunks

        if not isinstance(structure, dict):
            return chunks

        title = structure.get("title", "")
        path_titles = [*parents, title] if title else parents
        chunks.append(
            {
                "doc_name": doc_name,
                "source_path": source_path,
                "source_type": source_type,
                "title": title,
                "path": " > ".join(path_titles),
                "node_id": structure.get("node_id"),
                "summary": structure.get("summary") or structure.get("prefix_summary") or "",
                "text": structure.get("text") or "",
                "line_num": structure.get("line_num"),
                "start_index": structure.get("start_index"),
                "end_index": structure.get("end_index"),
            }
        )

        for child in structure.get("nodes", []) or []:
            chunks.extend(
                self._flatten_structure(
                    structure=child,
                    doc_name=doc_name,
                    source_path=source_path,
                    source_type=source_type,
                    parents=path_titles,
                )
            )
        return chunks

    def _score_chunk(
        self,
        chunk: Dict[str, Any],
        query_tokens: List[str],
        normalized_query: str,
    ) -> tuple[int, List[str]]:
        title = _normalize_text(chunk.get("title"))
        path = _normalize_text(chunk.get("path"))
        summary = _normalize_text(chunk.get("summary"))
        text = _normalize_text(chunk.get("text"))

        score = 0
        matched_terms: List[str] = []

        if normalized_query and normalized_query in title:
            score += 15
            matched_terms.append(normalized_query)
        elif normalized_query and normalized_query in text:
            score += 8
            matched_terms.append(normalized_query)

        for token in query_tokens:
            if token in title:
                score += 8
                matched_terms.append(token)
            elif token in path:
                score += 5
                matched_terms.append(token)
            elif token in summary:
                score += 4
                matched_terms.append(token)
            elif token in text:
                score += 2
                matched_terms.append(token)

        return score, _unique_keep_order(matched_terms)

    def _build_excerpt(self, chunk: Dict[str, Any], matched_terms: List[str], max_len: int = 280) -> str:
        base_text = str(chunk.get("text") or chunk.get("summary") or chunk.get("title") or "").strip()
        if not base_text:
            return ""

        lowered = base_text.lower()
        for token in matched_terms:
            position = lowered.find(token.lower())
            if position >= 0:
                start = max(position - 80, 0)
                end = min(position + max_len, len(base_text))
                snippet = base_text[start:end].strip()
                return snippet if start == 0 else f"...{snippet}"

        return base_text[:max_len].strip()
