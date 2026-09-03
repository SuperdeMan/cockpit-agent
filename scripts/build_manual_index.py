#!/usr/bin/env python3
"""从用户提供的车型手册 PDF 构建 deterministic 私有索引包。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.manual_rag.src.index_format import (
    ExtractedPage,
    build_index_bundle,
    write_index_bundle,
)


DEFAULT_ALIASES = ["SU7", "小米SU7", "Xiaomi SU7", "SU7 Pro", "SU7 Max"]
_PDF_DATE_RE = re.compile(r"^D:(\d{4})(\d{2})(\d{2})")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _revision(metadata: dict[str, Any], override: str) -> str:
    if override:
        return override
    value = str(metadata.get("/ModDate") or metadata.get("/CreationDate") or "")
    match = _PDF_DATE_RE.match(value)
    if not match:
        raise ValueError("PDF 没有可识别的 ModDate/CreationDate，请显式传 --revision")
    return "-".join(match.groups())


def _outline_entries(reader) -> list[tuple[int, int, int, str]]:
    entries: list[tuple[int, int, int, str]] = []
    order = 0

    def walk(items, depth: int = 0) -> None:
        nonlocal order
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = str(getattr(item, "title", "") or "").strip()
            if not title:
                continue
            try:
                page_number = reader.get_destination_page_number(item) + 1
            except Exception:
                continue
            entries.append((page_number, order, depth, title))
            order += 1

    walk(reader.outline)
    entries.sort(key=lambda value: (value[0], value[1]))
    return entries


def _paths_by_page(reader) -> tuple[dict[int, tuple[str, ...]], set[str]]:
    entries = _outline_entries(reader)
    hierarchy: list[str] = []
    cursor = 0
    result: dict[int, tuple[str, ...]] = {}
    top_level = {title for _, _, depth, title in entries if depth == 0}
    for page_number in range(1, len(reader.pages) + 1):
        page_paths: list[tuple[str, ...]] = []
        while cursor < len(entries) and entries[cursor][0] <= page_number:
            _, _, depth, title = entries[cursor]
            hierarchy = hierarchy[:depth]
            hierarchy.append(title)
            if entries[cursor][0] == page_number:
                page_paths.append(tuple(hierarchy))
            cursor += 1
        if not page_paths:
            result[page_number] = tuple(hierarchy)
            continue
        # 一个物理页可能同时开始两个同级章节（例如 PDF 245 同含“车轮与轮胎参数”
        # 和“四轮定位”）。取最后一个会把前半页引用错名；保留共同祖先并合并叶节点。
        leaves = [
            path for path in page_paths
            if not any(len(other) > len(path) and other[:len(path)] == path
                       for other in page_paths)
        ]
        if len(leaves) == 1:
            result[page_number] = leaves[0]
            continue
        common_length = 0
        for values in zip(*leaves):
            if len(set(values)) != 1:
                break
            common_length += 1
        suffixes = [" > ".join(path[common_length:]) for path in leaves]
        combined = " / ".join(dict.fromkeys(item for item in suffixes if item))
        result[page_number] = leaves[0][:common_length] + ((combined,) if combined else ())
    return result, top_level


def _clean_page_text(text: str, page_number: int,
                     top_level_titles: set[str]) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00ad", "").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if lines and lines[-1] == str(page_number):
        lines.pop()
        while lines and not lines[-1]:
            lines.pop()
        if lines and lines[-1] in top_level_titles:
            lines.pop()
    elif lines:
        for title in top_level_titles:
            if lines[-1] == f"{title} {page_number}":
                lines.pop()
                break
    cleaned = "\n".join(lines).strip()
    return _MULTI_BLANK_RE.sub("\n\n", cleaned)


def extract_pdf_pages(pdf_path: Path) -> tuple[list[ExtractedPage], dict[str, Any], int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "缺少构建期依赖 pypdf；请安装 agents/manual_rag/requirements-ingest.txt") from exc

    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        raise ValueError("不接受加密 PDF")
    paths, top_level = _paths_by_page(reader)
    pages: list[ExtractedPage] = []
    for page_number, page in enumerate(reader.pages, start=1):
        content = _clean_page_text(page.extract_text() or "", page_number, top_level)
        # 纯封面、空白页、只剩章节页脚的页不进入检索。
        if len(re.sub(r"\s+", "", content)) < 20:
            continue
        pages.append(ExtractedPage(page_number, paths[page_number], content))
    return pages, dict(reader.metadata or {}), len(reader.pages)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--document-id", default="xiaomi-su7-2024-user-manual")
    parser.add_argument("--title", default="")
    parser.add_argument("--publisher", default="小米汽车")
    parser.add_argument("--vehicle-model", default="xiaomi-su7-2024")
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--revision", default="")
    parser.add_argument("--expected-sha256", default="")
    parser.add_argument("--force", action="store_true",
                        help="显式允许覆盖已有生成索引")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pdf_path = args.pdf
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在：{pdf_path}")
    source_hash = _file_sha256(pdf_path)
    expected = args.expected_sha256.strip().lower()
    if expected and source_hash != expected:
        raise ValueError(
            f"源 PDF SHA-256 不符：expected={expected}, actual={source_hash}")

    pages, metadata, source_pages = extract_pdf_pages(pdf_path)
    title = args.title.strip() or str(metadata.get("/Title") or "").strip()
    if not title:
        raise ValueError("PDF 标题为空，请显式传 --title")
    bundle = build_index_bundle(
        pages,
        document_id=args.document_id,
        title=title,
        publisher=args.publisher,
        vehicle_model=args.vehicle_model,
        vehicle_aliases=args.alias or DEFAULT_ALIASES,
        revision=_revision(metadata, args.revision),
        source_file=pdf_path.name,
        source_sha256=source_hash,
        source_pages=source_pages,
    )
    output = write_index_bundle(args.output, bundle, overwrite=args.force)
    summary = {
        "status": "built",
        "output": str(output),
        "source_sha256": source_hash,
        "source_pages": source_pages,
        "indexed_chunks": len(bundle["chunks"]),
        "content_sha256": bundle["document"]["content_sha256"],
        "index_sha256": _file_sha256(output),
        "vehicle_model": bundle["document"]["vehicle_model"],
        "revision": bundle["document"]["revision"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
