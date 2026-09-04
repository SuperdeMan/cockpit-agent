#!/usr/bin/env python3
"""对真实车型手册执行源文件、全页、全目录、视觉与自然问法分层覆盖验证。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import glob
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Mapping
from types import SimpleNamespace

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "gen" / "python"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from agents.manual_rag.src.index_format import (  # noqa: E402
    LoadedManualPackage,
    build_index_bundle,
    build_visual_manifest,
    load_manual_package,
)
from agents.manual_rag.src.providers.local_index import (  # noqa: E402
    ManualIndexRetriever,
)
from scripts.build_manual_index import (  # noqa: E402
    _outline_entries,
    extract_pdf_pages,
    extract_pdf_visual_assets,
)
from scripts.eval_manual_rag import evaluate_cases  # noqa: E402
from agents._sdk.manifest import load_manifest  # noqa: E402
from orchestrator.cloud.models import Plan, Step  # noqa: E402
from orchestrator.cloud.planning import PlanBuilder  # noqa: E402
from orchestrator.cloud.route_hints import RouteHintEngine  # noqa: E402


_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_TOKEN_RE = re.compile(
    r"[\u3400-\u9fff]+|[a-z0-9]+(?:[._+/-][a-z0-9]+)*",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")


class CoverageContractError(ValueError):
    """全覆盖分母、digest 或来源重建与批准基线不一致。"""


def canonical_sha256(value: Any) -> str:
    encoded = (json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _coverage_tokens(value: str) -> list[str]:
    result: list[str] = []
    for part in _TOKEN_RE.findall(str(value or "").casefold()):
        if _CJK_RE.fullmatch(part):
            if len(part) == 1:
                continue
            result.extend(part[index:index + 2] for index in range(len(part) - 1))
        else:
            result.append(part)
    return result


def _display_topic(value: str) -> str:
    return str(value).replace("*", "").replace(" / ", "和").strip()


def build_section_cases(
    chunks: Iterable[Mapping[str, Any]],
    query_template: str,
    query_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    by_path: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for chunk in chunks:
        path = tuple(str(item) for item in chunk["section_path"])
        by_path[path].update(range(
            int(chunk["page_start"]), int(chunk["page_end"]) + 1))
    cases: list[dict[str, Any]] = []
    overrides = dict(query_overrides or {})
    for ordinal, (path, pages) in enumerate(sorted(by_path.items()), start=1):
        leaf = _display_topic(path[-1])
        parent = _display_topic(path[-2] if len(path) > 1 else path[-1])
        path_key = " > ".join(path)
        query = str(overrides.get(path_key) or query_template).format(
            parent=parent, leaf=leaf).strip()
        if not query:
            raise CoverageContractError(f"section query is empty: {path!r}")
        cases.append({
            "id": f"section-{ordinal:03d}-p{min(pages):04d}",
            "split": "section",
            "query": query,
            "expect_pages_any": sorted(pages),
            "expect_section_path": list(path),
        })
    return cases


def extract_outline_leaf_sections(pdf_path: Path) -> list[dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("outline audit requires pypdf") from exc
    reader = PdfReader(str(pdf_path))
    entries = sorted(_outline_entries(reader), key=lambda item: item[1])
    hierarchy: list[str] = []
    rows: list[tuple[int, int, tuple[str, ...]]] = []
    for page, _, depth, title in entries:
        hierarchy = hierarchy[:depth]
        hierarchy.append(title)
        rows.append((int(page), int(depth), tuple(hierarchy)))
    leaves: list[dict[str, Any]] = []
    for position, (page, depth, path) in enumerate(rows):
        next_depth = rows[position + 1][1] if position + 1 < len(rows) else -1
        if next_depth > depth:
            continue
        leaves.append({"page": page, "section_path": list(path)})
    return leaves


def build_outline_leaf_cases(
    leaves: Iterable[Mapping[str, Any]],
    query_template: str,
    query_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    overrides = dict(query_overrides or {})
    cases: list[dict[str, Any]] = []
    for ordinal, leaf_row in enumerate(leaves, start=1):
        path = tuple(str(item) for item in leaf_row["section_path"])
        page = int(leaf_row["page"])
        path_key = " > ".join(path)
        query = str(overrides.get(path_key) or query_template).format(
            parent=_display_topic(path[-2] if len(path) > 1 else path[-1]),
            leaf=_display_topic(path[-1]),
        ).strip()
        cases.append({
            "id": f"outline-{ordinal:03d}-p{page:04d}",
            "split": "outline_leaf",
            "query": query,
            "expect_pages_any": [page],
            "expect_section_terms_all": list(path),
            "outline_section_path": list(path),
        })
    return cases


def _anchor_line(
    chunk: Mapping[str, Any],
    *,
    total: int,
    document_frequency: Counter[str],
) -> str:
    candidates: list[tuple[float, str]] = []
    for raw in str(chunk["content"]).splitlines():
        line = _SPACE_RE.sub(" ", raw).strip(" •·-:：")
        compact = _SPACE_RE.sub("", line)
        if not 12 <= len(compact) <= 90:
            continue
        terms = set(_coverage_tokens(line))
        if len(terms) < 4:
            continue
        score = sum(
            math.log((total + 1) / (document_frequency[term] + 1))
            for term in terms
        ) / math.sqrt(len(terms))
        candidates.append((score, line))
    if candidates:
        return max(candidates, key=lambda item: (item[0], item[1]))[1]
    fallback = _SPACE_RE.sub(" ", str(chunk["content"])).strip()[:80]
    if not fallback:
        raise CoverageContractError(
            f"page {chunk.get('page_start')} has no usable anchor")
    return fallback


def build_page_anchor_cases(
    chunks: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [dict(chunk) for chunk in chunks]
    document_frequency: Counter[str] = Counter()
    for chunk in rows:
        document_frequency.update(set(_coverage_tokens(str(chunk["content"]))))
    cases: list[dict[str, Any]] = []
    for chunk in sorted(rows, key=lambda item: int(item["page_start"])):
        page = int(chunk["page_start"])
        cases.append({
            "id": f"page-anchor-{page:04d}",
            "split": "page_anchor",
            "query": _anchor_line(
                chunk,
                total=len(rows),
                document_frequency=document_frequency,
            ),
            "expect_pages_any": [page],
            "expect_section_path": list(chunk["section_path"]),
        })
    return cases


def build_visual_semantic_cases(
    catalog: Mapping[str, Any],
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[int]] = defaultdict(set)
    for page, raw in (catalog.get("table_pages") or {}).items():
        for label in raw.get("labels") or []:
            grouped[("warning", str(label["caption"]))].add(int(page))
    for item in catalog.get("asset_overrides") or []:
        grouped[("illustration", str(item["caption"]))].add(
            int(item["page_start"]))
    cases: list[dict[str, Any]] = []
    overrides = dict(config.get("visual_query_overrides") or {})
    for (kind, caption), pages in sorted(grouped.items()):
        template_key = (
            "visual_warning_query_template"
            if kind == "warning"
            else "visual_illustration_query_template"
        )
        query = str(
            overrides.get(f"{kind}:{caption}") or config[template_key]
        ).format(caption=caption).strip()
        digest = hashlib.sha256(
            f"{kind}:{caption}".encode("utf-8")).hexdigest()[:10]
        cases.append({
            "id": f"{kind}-{min(pages):04d}-{digest}",
            "split": "visual",
            "query": query,
            "expect_pages_any": sorted(pages),
            "expect_image_pages_any": sorted(pages),
            "expect_image_caption_all": [caption],
        })
    return cases


def _require_equal(name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise CoverageContractError(
            f"{name} mismatch: expected={expected!r}, actual={actual!r}")


def validate_inventory(
    index: Mapping[str, Any],
    visual: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    expected = config.get("expected") or {}
    document = index.get("document") or {}
    chunks = list(index.get("chunks") or [])
    pages = sorted({
        page
        for chunk in chunks
        for page in range(int(chunk["page_start"]), int(chunk["page_end"]) + 1)
    })
    chunk_ids = sorted(str(chunk["chunk_id"]) for chunk in chunks)
    paths = sorted({tuple(str(item) for item in chunk["section_path"])
                    for chunk in chunks})
    source_pages = int(document.get("source_pages") or 0)
    excluded_pages = sorted(set(range(1, source_pages + 1)) - set(pages))

    checks = {
        "source_pages": source_pages,
        "indexed_pages": len(pages),
        "excluded_text_pages": excluded_pages,
        "indexed_pages_sha256": canonical_sha256(pages),
        "chunk_ids_sha256": canonical_sha256(chunk_ids),
        "index_section_paths": len(paths),
        "index_section_paths_sha256": canonical_sha256(
            [list(path) for path in paths]),
        "visual_assets": int(visual.get("asset_count") or 0),
        "visual_blobs": int(visual.get("blob_count") or 0),
        "visual_skipped_assets": int(visual.get("skipped_asset_count") or 0),
    }
    for name, actual in checks.items():
        _require_equal(name, actual, expected.get(name))
    _require_equal(
        "content_sha256",
        document.get("content_sha256"),
        config.get("content_sha256"),
    )
    _require_equal(
        "visual_assets_sha256",
        visual.get("assets_sha256"),
        config.get("visual_assets_sha256"),
    )
    return checks


def validate_outline_inventory(
    leaves: list[dict[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    expected = config.get("expected") or {}
    summary = {
        "outline_leaf_sections": len(leaves),
        "outline_leaf_sections_sha256": canonical_sha256(leaves),
    }
    for name, actual in summary.items():
        _require_equal(name, actual, expected.get(name))
    return summary


def evaluate_explicit_route_hints(
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    agent_map: dict[str, Any] = {}
    for path in sorted(glob.glob(str(REPO_ROOT / "agents/*/manifest.yaml"))):
        manifest = load_manifest(path)
        agent_map[manifest.agent_id] = SimpleNamespace(
            manifest=manifest, endpoint=f"{manifest.agent_id}:0")
    failures: list[dict[str, Any]] = []
    for case in cases:
        plan = Plan(steps=[Step(
            id="seed", agent_id="chitchat", intent="chitchat.talk")])
        RouteHintEngine(PlanBuilder._validated_steps).apply(
            plan, str(case["query"]), agent_map)
        actual = [step.intent for step in plan.steps]
        if actual != ["manual.query"]:
            failures.append({"id": case["id"], "actual": actual})
    return {
        "total": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "failures": failures,
    }


def audit_source_rebuild(
    pdf_path: Path,
    package: LoadedManualPackage,
    visual_catalog_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    document = package.index["document"]
    source_hash = file_sha256(pdf_path)
    _require_equal("source_sha256", source_hash, document["source_sha256"])
    pages, _, source_pages = extract_pdf_pages(pdf_path)
    rebuilt_index = build_index_bundle(
        pages,
        document_id=document["document_id"],
        title=document["title"],
        publisher=document["publisher"],
        vehicle_model=document["vehicle_model"],
        vehicle_aliases=document["vehicle_aliases"],
        revision=document["revision"],
        source_file=pdf_path.name,
        source_sha256=source_hash,
        source_pages=source_pages,
    )
    _require_equal("rebuilt_index", rebuilt_index, package.index)

    assets, skipped = extract_pdf_visual_assets(
        pdf_path,
        document_id=document["document_id"],
        source_sha256=source_hash,
        catalog_path=visual_catalog_path,
    )
    rebuilt_visual, blobs = build_visual_manifest(
        assets,
        document_id=document["document_id"],
        source_sha256=source_hash,
        skipped_assets=skipped,
    )
    _require_equal("rebuilt_visual_manifest", rebuilt_visual, package.visual)
    for asset in rebuilt_visual["assets"]:
        expected = blobs[asset["blob_path"]]
        actual = package.read_asset(asset["asset_id"])
        if actual != expected:
            raise CoverageContractError(
                f"rebuilt visual blob differs: {asset['asset_id']}")
    indexed_pages = {page.page_number for page in pages}
    return {
        "source_sha256": source_hash,
        "source_pages": source_pages,
        "indexed_pages": len(pages),
        "excluded_text_pages": sorted(
            set(range(1, source_pages + 1)) - indexed_pages),
        "visual_assets": rebuilt_visual["asset_count"],
        "visual_blobs": rebuilt_visual["blob_count"],
        "visual_skipped_assets": rebuilt_visual["skipped_asset_count"],
        "index_exact": True,
        "visual_manifest_exact": True,
        "visual_blobs_exact": True,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _top_rank_summary(report: Mapping[str, Any], cases: list[dict[str, Any]]) -> dict[str, int]:
    top1 = 0
    by_id = {str(case["id"]): case for case in cases}
    for result in report.get("cases") or []:
        expected = by_id[str(result["id"])]
        hits = result.get("hits") or []
        if not hits:
            continue
        expected_pages = set(int(page) for page in expected.get("expect_pages_any") or [])
        expected_section = tuple(expected.get("expect_section_path") or ())
        first = hits[0]
        page_ok = int(first["page_start"]) in expected_pages if expected_pages else True
        section_ok = (
            tuple(first.get("section_path") or ()) == expected_section
            if expected_section else True
        )
        if page_ok and section_ok:
            top1 += 1
    return {
        "top1": top1,
        "top_k": int(report["summary"]["passed"]),
        "total": len(cases),
    }


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise CoverageContractError(f"YAML root must be object: {path}")
    return raw


async def evaluate_full_coverage(
    *,
    index_path: Path,
    pdf_path: Path,
    config_path: Path,
) -> dict[str, Any]:
    config = _load_yaml(config_path)
    if config.get("version") != 1:
        raise CoverageContractError("full coverage config version must be 1")
    package_hash = file_sha256(index_path)
    _require_equal("package_sha256", package_hash, config.get("package_sha256"))
    package = load_manual_package(index_path)
    _require_equal(
        "document_id",
        package.index["document"]["document_id"],
        config.get("document_id"),
    )
    _require_equal(
        "vehicle_model",
        package.index["document"]["vehicle_model"],
        config.get("vehicle_model"),
    )
    _require_equal(
        "source_sha256",
        package.index["document"]["source_sha256"],
        config.get("source_sha256"),
    )
    inventory = validate_inventory(package.index, package.visual, config)

    visual_catalog_path = REPO_ROOT / str(config["visual_catalog"])
    natural_corpus_path = REPO_ROOT / str(config["natural_corpus"])
    source_rebuild = audit_source_rebuild(
        pdf_path, package, visual_catalog_path)
    outline_leaves = extract_outline_leaf_sections(pdf_path)
    outline_inventory = validate_outline_inventory(outline_leaves, config)
    retriever = ManualIndexRetriever(
        index_path,
        vehicle_model=str(config["vehicle_model"]),
    )
    chunks = package.index["chunks"]
    top_k = int(config.get("top_k", 4))

    page_cases = build_page_anchor_cases(chunks)
    section_cases = build_section_cases(
        chunks,
        str(config["section_query_template"]),
        config.get("section_query_overrides") or {},
    )
    outline_cases = build_outline_leaf_cases(
        outline_leaves,
        str(config["section_query_template"]),
        config.get("section_query_overrides") or {},
    )
    visual_cases = build_visual_semantic_cases(
        _load_yaml(visual_catalog_path), config)
    natural_config = _load_yaml(natural_corpus_path)
    natural_cases = list(natural_config.get("cases") or [])
    explicit_route_hints = evaluate_explicit_route_hints(
        outline_cases + visual_cases)

    expected = config["expected"]
    _require_equal("page_anchor_cases", len(page_cases), expected["indexed_pages"])
    _require_equal(
        "section_cases", len(section_cases), expected["index_section_paths"])
    _require_equal(
        "outline_cases", len(outline_cases), expected["outline_leaf_sections"])
    _require_equal(
        "visual_semantic_topics",
        len(visual_cases),
        expected["visual_semantic_topics"],
    )
    _require_equal("natural_cases", len(natural_cases), expected["natural_cases"])

    page_report = await evaluate_cases(
        retriever, page_cases, top_k=top_k,
        vehicle_model=str(config["vehicle_model"]))
    section_report = await evaluate_cases(
        retriever, section_cases, top_k=top_k,
        vehicle_model=str(config["vehicle_model"]))
    outline_report = await evaluate_cases(
        retriever, outline_cases, top_k=top_k,
        vehicle_model=str(config["vehicle_model"]))
    visual_report = await evaluate_cases(
        retriever, visual_cases, top_k=top_k,
        vehicle_model=str(config["vehicle_model"]))
    natural_report = await evaluate_cases(
        retriever, natural_cases, top_k=top_k,
        vehicle_model=str(config["vehicle_model"]))

    layers = {
        "page_anchor": page_report,
        "section_canonical": section_report,
        "outline_leaf": outline_report,
        "visual_semantic": visual_report,
        "natural_regression": natural_report,
    }
    failed_layers = {
        name: int(report["summary"]["failed"])
        for name, report in layers.items()
        if report["summary"]["failed"]
    }
    if explicit_route_hints["failed"]:
        failed_layers["explicit_route_hint"] = explicit_route_hints["failed"]
    return {
        "schema_version": 1,
        "status": "passed" if not failed_layers else "failed",
        "index": {
            "path": str(index_path),
            "package_sha256": package_hash,
            "document_id": package.index["document"]["document_id"],
            "vehicle_model": package.index["document"]["vehicle_model"],
        },
        "inventory": inventory,
        "outline_inventory": outline_inventory,
        "source_rebuild": source_rebuild,
        "explicit_route_hint": explicit_route_hints,
        "summary": {
            "failed_layers": failed_layers,
            "page_anchor": _top_rank_summary(page_report, page_cases),
            "section_canonical": _top_rank_summary(section_report, section_cases),
            "outline_leaf": _top_rank_summary(outline_report, outline_cases),
            "visual_semantic": {
                "passed": visual_report["summary"]["passed"],
                "total": len(visual_cases),
            },
            "natural_regression": {
                "passed": natural_report["summary"]["passed"],
                "total": len(natural_cases),
            },
            "explicit_route_hint": {
                "passed": explicit_route_hints["passed"],
                "total": explicit_route_hints["total"],
            },
        },
        "layers": layers,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        type=Path,
        default=REPO_ROOT / "models/manual_rag/xiaomi-su7-2024.v2.mrag",
    )
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "test/eval_corpus/manual_rag_full_coverage.yaml",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(evaluate_full_coverage(
        index_path=args.index,
        pdf_path=args.pdf,
        config_path=args.config,
    ))
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if args.output:
        print(f"artifact={args.output}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
