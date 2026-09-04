#!/usr/bin/env python3
"""对当前真栈运行整本手册目录/受控视觉覆盖探针。

本脚本只发送问句，不确认、不调用 debug 写接口。每轮都要求单一 manual 卡、approved real
provenance、预期页/section/image、零 action、零 need_confirm，并在 collector 回读完整车态。
任一轮产生车态差异即停止，不自动发反向车控。
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
for import_root in (REPO_ROOT, REPO_ROOT / "test", REPO_ROOT / "gen" / "python"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from agents.manual_rag.src.index_format import load_manual_package  # noqa: E402
from scripts.dev_stack_lib import read_root_env  # noqa: E402
from scripts.e2e_target import endpoint_environment, resolve_e2e_target  # noqa: E402
from scripts.eval_manual_rag_full_coverage import (  # noqa: E402
    build_section_cases,
    build_outline_leaf_cases,
    build_visual_semantic_cases,
    canonical_sha256,
    extract_outline_leaf_sections,
    validate_outline_inventory,
    validate_inventory,
)
from orchestrator.edge.fast_intent import classify_structured  # noqa: E402
from runtime.question_shape import is_non_directive_question  # noqa: E402
from support.manual_rag_contract import (  # noqa: E402
    cards,
    manual_response_errors,
)

try:
    import websockets
except ImportError:
    print("请先安装项目运行依赖 websockets")
    raise


_HELLO_WAIT_S = 2.0
_FINAL_TIMEOUT_S = 120.0
_TAIL_IDLE_S = 0.6
_TAIL_BUDGET_S = 25.0
_MAX_WS_BYTES = 8 * 1024 * 1024


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"YAML root must be object: {path}")
    return value


def build_live_cases(
    index: Mapping[str, Any],
    config: Mapping[str, Any],
    visual_catalog: Mapping[str, Any],
    outline_leaves: list[dict[str, Any]],
    *,
    kinds: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if "section" in kinds:
        result.extend(build_outline_leaf_cases(
            outline_leaves,
            str(config["section_query_template"]),
            config.get("section_query_overrides") or {},
        ))
    if "visual" in kinds:
        result.extend(build_visual_semantic_cases(visual_catalog, config))
    return result


def failure_ids_from_artifact(
    path: Path,
    *,
    expected_release: str,
) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("expected_release_sha") != expected_release:
        raise RuntimeError("failure source release mismatch")
    ids = {
        str(row["id"]) for row in payload.get("rows") or []
        if not row.get("passed")
    }
    if not ids:
        raise RuntimeError("failure source has no failed rows")
    return ids


def _runtime_endpoints(env_root: Path) -> tuple[str, str]:
    environment = dict(os.environ)
    environment.update(read_root_env(
        env_root, {"TAILNET_FQDN", "VITE_WS_TOKEN"}))
    target = resolve_e2e_target(
        REPO_ROOT, explicit=None, environ=environment)
    endpoints = endpoint_environment(target)
    ws_url = endpoints["WS_URL"]
    if target.name == "cloud":
        token = str(environment.get("VITE_WS_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("cloud target requires VITE_WS_TOKEN from root .env")
        parts = urllib.parse.urlsplit(ws_url)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query.append(("token", token))
        ws_url = urllib.parse.urlunsplit(parts._replace(
            query=urllib.parse.urlencode(query)))
    return ws_url, endpoints["COLLECTOR_URL"]


def _http_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


async def _vehicle_state(collector_url: str) -> dict[str, Any]:
    value = await asyncio.to_thread(
        _http_json, f"{collector_url}/api/vehicle/state")
    if not isinstance(value, dict) or not value:
        raise RuntimeError("collector vehicle state is empty")
    if not all(isinstance(key, str) for key in value):
        raise RuntimeError("collector vehicle state has invalid keys")
    return dict(sorted(value.items()))


def validate_live_query_safety(cases_to_run: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for case in cases_to_run:
        query = str(case["query"])
        edge = classify_structured(query)
        question = is_non_directive_question(query)
        if edge is not None or not question:
            failures.append({
                "id": case["id"],
                "query": query,
                "question": question,
                "edge_intent": edge,
            })
    if failures:
        raise RuntimeError(
            "live query safety preflight failed: "
            + json.dumps(failures, ensure_ascii=False))
    return {
        "total": len(cases_to_run),
        "question_shape_passed": len(cases_to_run),
        "fast_intent_none": len(cases_to_run),
    }


def _flatten_cards(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    return cards(message)


def merge_finals(first: Mapping[str, Any], later: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(first)
    result["actions"] = list(first.get("actions") or []) + list(
        later.get("actions") or [])
    result["need_confirm"] = bool(
        first.get("need_confirm") or later.get("need_confirm"))
    speeches = [str(item) for item in (
        first.get("speech"), later.get("speech")) if item]
    result["speech"] = "\n".join(speeches)
    merged_cards = _flatten_cards(first) + _flatten_cards(later)
    if len(merged_cards) == 1:
        result["ui_card"] = merged_cards[0]
    elif merged_cards:
        result["ui_card"] = {"type": "card_group", "items": merged_cards}
    return result


async def _ask(
    ws_url: str,
    *,
    text: str,
    session_id: str,
    trace_id: str,
    provider: str,
    model: str,
) -> dict[str, Any]:
    async with websockets.connect(
        ws_url,
        max_size=_MAX_WS_BYTES,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=_HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        await ws.send(json.dumps({
            "text": text,
            "session_id": session_id,
            "meta": {
                "current_lat": "22.5410",
                "current_lng": "113.9412",
                "vehicle_model": "xiaomi-su7-2024",
                "llm_provider": provider,
                "llm_model": model,
                "trace_id": trace_id,
            },
        }, ensure_ascii=False))
        merged: dict[str, Any] | None = None
        timeout = _FINAL_TIMEOUT_S
        deadline = 0.0
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                if merged is not None:
                    return merged
                raise
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "final":
                merged = (dict(message) if merged is None
                          else merge_finals(merged, message))
                timeout = _TAIL_IDLE_S
                if not deadline:
                    deadline = time.monotonic() + _TAIL_BUDGET_S
            elif kind == "error":
                error = {
                    "type": "error",
                    "speech": str(message.get("message") or "unknown error"),
                    "actions": [],
                    "need_confirm": False,
                    "error": True,
                }
                return error if merged is None else merge_finals(merged, error)
            elif merged is not None:
                timeout = max(
                    0.1,
                    min(_FINAL_TIMEOUT_S, deadline - time.monotonic()),
                )


def sanitize_message(message: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(message))
    for card in cards(result):
        for image in card.get("images") or []:
            if not isinstance(image, dict):
                continue
            data_uri = str(image.pop("data_uri", "") or "")
            image["data_uri_chars"] = len(data_uri)
    return result


def _state_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }


def summarize(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(payload.get("rows") or [])
    kinds = sorted({str(row.get("kind")) for row in rows})
    return {
        "scheduled": int(payload.get("scheduled") or 0),
        "executed": len(rows),
        "passed": sum(bool(row.get("passed")) for row in rows),
        "failed": sum(not bool(row.get("passed")) for row in rows),
        "by_kind": {
            kind: {
                "total": sum(row.get("kind") == kind for row in rows),
                "passed": sum(
                    row.get("kind") == kind and bool(row.get("passed"))
                    for row in rows),
            }
            for kind in kinds
        },
        "failed_ids": [str(row.get("id")) for row in rows
                       if not row.get("passed")],
        "action_rows": [str(row.get("id")) for row in rows
                        if row.get("actions")],
        "confirmation_rows": [str(row.get("id")) for row in rows
                              if row.get("need_confirm")],
        "probe_errors": [str(row.get("id")) for row in rows
                         if row.get("probe_error")],
        "state_diff": _state_diff(
            payload.get("state_before") or {},
            payload.get("state_after") or {},
        ),
    }


def _write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    payload["summary"] = summarize(payload)
    temp = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


async def run(args: argparse.Namespace) -> int:
    config = _load_yaml(args.config)
    package = load_manual_package(args.index)
    validate_inventory(package.index, package.visual, config)
    outline_leaves = extract_outline_leaf_sections(args.pdf)
    validate_outline_inventory(outline_leaves, config)
    visual_catalog = _load_yaml(REPO_ROOT / str(config["visual_catalog"]))
    kinds = set(args.kind or ("section", "visual"))
    cases_to_run = build_live_cases(
        package.index, config, visual_catalog, outline_leaves, kinds=kinds)
    if args.retry_failures_from is not None:
        requested_ids = failure_ids_from_artifact(
            args.retry_failures_from,
            expected_release=args.expected_release,
        )
        known_ids = {str(case["id"]) for case in cases_to_run}
        missing_ids = requested_ids - known_ids
        if missing_ids:
            raise RuntimeError(
                f"failure source contains unknown cases: {sorted(missing_ids)}")
        cases_to_run = [
            case for case in cases_to_run if case["id"] in requested_ids]
    if args.start:
        cases_to_run = cases_to_run[args.start:]
    if args.limit is not None:
        cases_to_run = cases_to_run[:args.limit]
    safety_preflight = validate_live_query_safety(cases_to_run)
    case_digest = canonical_sha256(cases_to_run)
    approved_catalog = _load_yaml(
        REPO_ROOT / "agents/manual_rag/resources/manual_catalog.yaml")
    approved_document = dict(
        approved_catalog["documents"][config["document_id"]])
    ws_url, collector_url = _runtime_endpoints(args.env_root)
    baseline = await _vehicle_state(collector_url)
    run_id = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expected_release_sha": args.expected_release,
        "provider": args.provider,
        "model": args.model,
        "case_digest": case_digest,
        "scheduled": len(cases_to_run),
        "safety_preflight": safety_preflight,
        "state_before": baseline,
        "state_after": baseline,
        "rows": [],
    }
    if args.resume and args.output.is_file():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        for key, expected in (
            ("expected_release_sha", args.expected_release),
            ("provider", args.provider),
            ("model", args.model),
            ("case_digest", case_digest),
        ):
            if existing.get(key) != expected:
                raise RuntimeError(f"resume {key} mismatch")
        if existing.get("state_before") != baseline:
            raise RuntimeError("resume vehicle baseline changed")
        payload = existing
        run_id = str(payload["run_id"])
    done = {str(row["id"]) for row in payload["rows"]}
    consecutive_errors = 0
    for ordinal, case in enumerate(cases_to_run, start=1):
        if case["id"] in done:
            continue
        trace_id = f"manual-full-{run_id[:10]}-{ordinal:03d}"
        session_id = f"manual-full-{run_id[:10]}-{ordinal:03d}"
        started = time.perf_counter()
        probe_error = ""
        try:
            message = await _ask(
                ws_url,
                text=str(case["query"]),
                session_id=session_id,
                trace_id=trace_id,
                provider=args.provider,
                model=args.model,
            )
            errors = manual_response_errors(
                message, case, approved_document)
        except Exception as exc:
            message = {"actions": [], "need_confirm": False}
            probe_error = f"{type(exc).__name__}: {exc}"
            errors = [probe_error]
        current_state = await _vehicle_state(collector_url)
        state_diff = _state_diff(baseline, current_state)
        if state_diff:
            errors.append(f"vehicle state changed: {state_diff}")
        row = {
            "id": case["id"],
            "kind": case["split"],
            "query": case["query"],
            "passed": not errors,
            "errors": errors,
            "probe_error": probe_error,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "trace_id": trace_id,
            "actions": list(message.get("actions") or []),
            "need_confirm": bool(message.get("need_confirm")),
            "state_diff_from_start": state_diff,
            "response": sanitize_message(message),
        }
        payload["rows"].append(row)
        payload["state_after"] = current_state
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_checkpoint(args.output, payload)
        print(
            f"[{len(payload['rows'])}/{len(cases_to_run)}] "
            f"{case['id']} {'PASS' if row['passed'] else 'FAIL'} "
            f"{row['latency_ms']:.0f}ms",
            flush=True,
        )
        consecutive_errors = consecutive_errors + 1 if probe_error else 0
        unsafe_response = bool(row["actions"] or row["need_confirm"])
        if state_diff or unsafe_response:
            reason = (
                "vehicle state changed" if state_diff
                else "response returned action or confirmation"
            )
            print(f"ABORT: {reason}; no reverse action is sent", flush=True)
            break
        if consecutive_errors >= args.max_consecutive_errors:
            print("ABORT: consecutive probe errors reached the limit", flush=True)
            break
        if args.pause_ms:
            await asyncio.sleep(args.pause_ms / 1000)
    payload["state_after"] = await _vehicle_state(collector_url)
    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_checkpoint(args.output, payload)
    summary = payload["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    complete = summary["executed"] == summary["scheduled"]
    return 0 if complete and summary["failed"] == 0 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-root", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--expected-release", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--index",
        type=Path,
        default=REPO_ROOT / "models/manual_rag/xiaomi-su7-2024.v2.mrag",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "test/eval_corpus/manual_rag_full_coverage.yaml",
    )
    parser.add_argument(
        "--kind", action="append", choices=("section", "visual"), default=[])
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failures-from", type=Path)
    parser.add_argument("--pause-ms", type=int, default=200)
    parser.add_argument("--max-consecutive-errors", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.start < 0 or args.limit is not None and args.limit < 1:
        raise SystemExit("--start must be >=0 and --limit must be positive")
    if args.pause_ms < 0 or not 1 <= args.max_consecutive_errors <= 10:
        raise SystemExit("pause/error limits are invalid")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
