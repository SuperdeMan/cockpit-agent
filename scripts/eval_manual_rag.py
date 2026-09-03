#!/usr/bin/env python3
"""对私有车型手册索引运行确定性 retrieval golden cases。"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable
import unicodedata

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "gen" / "python"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from agents.manual_rag.src.providers.local_index import ManualIndexRetriever


def _pages(chunks: Iterable[Any]) -> set[int]:
    result: set[int] = set()
    for chunk in chunks:
        result.update(range(int(chunk.page_start), int(chunk.page_end) + 1))
    return result


def _comparable(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize(
        "NFKC", str(value or "")).casefold())


async def evaluate_cases(retriever, cases: list[dict[str, Any]], *, top_k: int = 4,
                         vehicle_model: str = "") -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    latencies: list[float] = []
    for case in cases:
        case_id = str(case.get("id") or "").strip()
        split = str(case.get("split") or "main").strip()
        query = str(case.get("query") or "").strip()
        started = time.perf_counter()
        chunks = await retriever.retrieve(
            query, vehicle_model=vehicle_model, top_k=top_k)
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        actual_pages = _pages(chunks)
        images = [image for chunk in chunks for image in getattr(chunk, "images", ())]
        image_pages = {int(image.page_start) for image in images}
        image_captions = _comparable("\n".join(image.caption for image in images))
        combined = "\n".join(chunk.content for chunk in chunks)
        comparable = _comparable(combined)
        reasons: list[str] = []
        expect_empty = bool(case.get("expect_empty", False))
        if expect_empty:
            if chunks:
                reasons.append(f"expected empty, got pages {sorted(actual_pages)}")
        else:
            if not chunks:
                reasons.append("expected hits, got empty")
            if any(getattr(chunk, "source_type", "") != "manual" for chunk in chunks):
                reasons.append("non-manual source returned")
            expected_pages = {int(page) for page in case.get("expect_pages_any") or []}
            if expected_pages and not expected_pages.intersection(actual_pages):
                reasons.append(
                    f"missing expected page, want any {sorted(expected_pages)}, "
                    f"got {sorted(actual_pages)}")
            required_pages = {int(page) for page in case.get("expect_pages_all") or []}
            missing_pages = required_pages - actual_pages
            if missing_pages:
                reasons.append(
                    f"missing required pages {sorted(missing_pages)}, "
                    f"got {sorted(actual_pages)}")
            expected_top = case.get("expect_top_page")
            if expected_top is not None:
                top_pages = _pages(chunks[:1])
                if int(expected_top) not in top_pages:
                    reasons.append(
                        f"wrong top page, want {int(expected_top)}, "
                        f"got {sorted(top_pages)}")
            missing_all = [str(item) for item in case.get("expect_text_all") or []
                           if _comparable(item) not in comparable]
            if missing_all:
                reasons.append(f"missing text: {missing_all}")
            expected_any = [str(item) for item in case.get("expect_text_any") or []]
            if expected_any and not any(
                    _comparable(item) in comparable for item in expected_any):
                reasons.append(f"missing any text: {expected_any}")
            expected_image_pages = {
                int(page) for page in case.get("expect_image_pages_any") or []}
            if expected_image_pages and not expected_image_pages.intersection(image_pages):
                reasons.append(
                    f"missing expected image page, want any "
                    f"{sorted(expected_image_pages)}, got {sorted(image_pages)}")
            missing_image_captions = [
                str(item) for item in case.get("expect_image_caption_all") or []
                if _comparable(item) not in image_captions
            ]
            if missing_image_captions:
                reasons.append(
                    f"missing image caption: {missing_image_captions}")

        passed = not reasons
        results.append({
            "id": case_id,
            "split": split,
            "query": query,
            "passed": passed,
            "reason": "; ".join(reasons),
            "latency_ms": round(latency_ms, 3),
            "pages": sorted(actual_pages),
            "hits": [{
                "source": chunk.source,
                "score": round(float(chunk.score), 6),
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "excerpt": " ".join(chunk.content.split())[:180],
            } for chunk in chunks],
            "images": [{
                "asset_id": image.asset_id,
                "caption": image.caption,
                "page_start": image.page_start,
                "media_type": image.media_type,
                "sha256": image.sha256,
                "match_kind": image.match_kind,
            } for image in images],
        })
    passed_count = sum(item["passed"] for item in results)
    split_summary: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = split_summary.setdefault(
            item["split"], {"total": 0, "passed": 0, "failed": 0})
        bucket["total"] += 1
        bucket["passed" if item["passed"] else "failed"] += 1
    ordered = sorted(latencies)
    percentile_95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)] \
        if ordered else 0.0
    return {
        "summary": {
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "splits": split_summary,
            "latency_ms": {
                "p50": round(ordered[len(ordered) // 2], 3) if ordered else 0.0,
                "p95": round(percentile_95, 3),
                "max": round(max(ordered), 3) if ordered else 0.0,
            },
        },
        "cases": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = yaml.safe_load(args.cases.read_text(encoding="utf-8")) or {}
    if config.get("version") != 1 or not isinstance(config.get("cases"), list):
        raise ValueError("评测集 version/cases 非法")
    top_k = int(config.get("top_k", 4))
    vehicle_model = str(config.get("vehicle_model") or "")
    retriever = ManualIndexRetriever(args.index, vehicle_model=vehicle_model)
    report = asyncio.run(evaluate_cases(
        retriever, config["cases"], top_k=top_k, vehicle_model=vehicle_model))
    report["index"] = {
        "path": str(args.index),
        "document_id": retriever.document["document_id"],
        "source_sha256": retriever.document["source_sha256"],
        "content_sha256": retriever.document["content_sha256"],
        "vehicle_model": retriever.vehicle_model,
        "revision": retriever.revision,
        "visual_asset_count": len(getattr(retriever, "visual_assets", ())),
        "visual_assets_sha256": str(
            getattr(retriever, "visual_manifest", {}).get("assets_sha256") or ""),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if report["summary"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
