"""CA2-01/JV00: frozen, synthetic, zero-execution baseline for the v2 migration.

Reuses the established signed identity, WS turn, trace-settling and release probes.
Never confirms an operation or restores a changed vehicle. An unexpected action or
vehicle difference stops the run. Raw artifacts stay in .artifacts, not in Git.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from scripts import probe_history_window as identity
from scripts import probe_qa_regression as wire
from scripts import probe_qa_long_sessions as audit
from scripts.e2e_identity import sign_identity
from scripts.journey_expect import EXPECT_KEYS as JOURNEY_EXPECT_KEYS, check_expect as journey_check
from scripts.render_cloud_env import DEMO_AUTH_SCOPES

CORPUS = ROOT / "test/eval_corpus/v2_runtime/seed.yaml"
#: 冻结要求「已提交、干净」的范围：这次跑数真正读进来的东西——运行器与它导入的 scripts/、runtime/，语料，判定读的来源证据表。
#: 资产树从发布 SHA 读（`git rev-parse <sha>:<path>`），不读工作树。主仓库是多个会话共享的工作树，别处未提交的文档不影响这次跑数，
#: 却曾挡住基线（2026-10-09：一份 Android 记录、一份可观测台 brief）。新加的导入或读取的文件要落在这里面（测试钉着）。
FREEZE_INPUTS = ("scripts", "runtime", "test/eval_corpus", "test/journeys",
                 "agents/manual_rag/resources/source_evidence.yaml")
#: 输入目录里的测试不进跑数（运行器不导入它们）：别的会话常在改它们（2026-10-09 P1 跑数期间，容量回收与保留策略的测试改了一半），
#: 不该判成「跑数输入变了」。
FREEZE_EXCLUDES = ("scripts/tests", "runtime/tests")
_FREEZE_PATHSPEC = FREEZE_INPUTS + tuple(f":(exclude){p}" for p in FREEZE_EXCLUDES)
#: 核心旅程冻结清单（docs/design/2026-10-09-v2-core-journey-freeze.md）：只登记引用与分类，旅程内容在 source 指向的文件里。
#: 7 月旅程（test/journeys/）也是跑数输入——清单引用它们时一并进 `FREEZE_INPUTS`。
MANIFEST = ROOT / "test/eval_corpus/v2_runtime/core/manifest.yaml"
FAMILIES = {f"F{n:02d}" for n in range(1, 13)}
LANES = ("read_only", "synthetic_writes", "simulated_vehicle")
#: 7 月旅程在 v2 运行器里认的原语：一轮只有 `say` + `expect`，前置只有 `setup.location`；别的记进 `unsupported`，跑数时记「未测」
_JOURNEY_TURN_KEYS = {"say", "expect"}
#: 条目级 meta 只认这些键：F12 受话与拒识要按语音来源发（`voice_*` / `ptt`，与编排 `is_voice_input_source` 同一口径）。
#: 位置走 7 月旅程的 `setup.location`；别的键一律拒绝——meta 是下发给编排的，不许语料随手塞别的东西。
_CASE_META_KEYS = {"input_source"}
SCOPES = tuple(s for s in DEMO_AUTH_SCOPES if s not in {"merchant.write", "payment.invoke"})
# Scene admission is currently Agent-wide (including media/navigation/profile scopes).
# Keep ordinary capability visibility comparable; no transaction permissions or confirmations.
ASSET_GROUPS = {
    "protocol": ("proto",), "planning": ("orchestrator/cloud", "skills"),
    "capabilities": ("agents", "orchestrator/edge/knowledge"),
    "policy": ("runtime", "security"), "gateway": ("llm-gateway", "gateway"),
    "manual_catalog": ("agents/manual_rag/resources",),
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cases(corpus: Path = CORPUS) -> list[dict]:
    data = yaml.safe_load(corpus.read_text(encoding="utf-8"))
    cases = data["cases"]
    if data.get("version") != 1 or data.get("split") != "regression":
        raise ValueError("expected versioned regression corpus")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids) or not cases:
        raise ValueError("duplicate/empty cases")
    for case in cases:
        validate_case(case)
    return cases


def validate_case(case: dict) -> None:
    """冻结语料的静态护栏（v2 语料与清单引用的 7 月旅程同一份）：从不确认、取消只说「取消」、不许肯定答复。"""
    if not case.get("family") or not case.get("turns"):
        raise ValueError("case requires family and turns")
    meta = case.get("meta") or {}
    if set(meta) - _CASE_META_KEYS:
        raise ValueError("case meta only takes input_source")
    source = str(meta.get("input_source") or "")
    if "input_source" in meta and not (source.startswith("voice_") or source == "ptt"):
        raise ValueError("input_source must be a voice source (voice_* or ptt)")
    for turn in case["turns"]:
        if turn.get("confirm") or turn.get("is_confirmation"):
            raise ValueError("baseline must never confirm")
        if turn.get("cancel_pending") and turn["say"] != "取消":
            raise ValueError("cleanup must use an exact addressed cancellation")
        if re.fullmatch(r"(?:确认|好的|可以|同意|执行)[。！!\s]*", str(turn.get("say") or "")):
            raise ValueError("affirmation is not allowed in this baseline")


def _adapt_journey(journey: dict) -> dict:
    """7 月旅程 → v2 case：一轮 `say` + `expect`（判据键由 `scripts.journey_expect` 认），前置位置覆盖这次会话的当前位置；
    其余原语（确认、按钮、等推送、车态前置、收尾……）记进 `unsupported`，跑数时整条记「未测」。`requires` 只记依赖的外部服务。"""
    setup = journey.get("setup") or {}
    unsupported = {f"setup.{k}" for k in setup if k != "location"}
    unsupported |= {k for k in ("final_vehicle", "cleanup") if journey.get(k)}
    for turn in journey.get("turns") or []:
        unsupported |= {k for k in turn if k not in _JOURNEY_TURN_KEYS}
        for branch in [turn.get("expect") or {}, *((turn.get("expect") or {}).get("any_of") or [])]:
            if "process_min" in branch:
                unsupported.add("expect.process_min")      # v2 收发层不采过程区事件
    case = {"id": journey["id"], "family": "", "source_kind": "journey",
            "turns": [{"say": str(t["say"]), "expect": dict(t.get("expect") or {})}
                      for t in journey.get("turns") or [] if "say" in t],
            "providers": sorted({p for r in journey.get("requires") or [] for p in str(r).split("|")})}
    location = setup.get("location")
    if isinstance(location, dict) and "lat" in location and "lng" in location:
        case["location"] = {"lat": str(location["lat"]), "lng": str(location["lng"])}
    if unsupported:
        case["unsupported"] = sorted(unsupported)
    return case


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    """按清单逐条取旅程：来源是 v2 语料（原 schema）或 7 月旅程（经 `_adapt_journey`），分类以清单为准。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("split") != "regression":
        raise ValueError("expected versioned regression manifest")
    entries = data.get("journeys") or []
    ids = [e.get("id") for e in entries]
    if not entries or len(set(ids)) != len(ids):
        raise ValueError("duplicate/empty journeys")
    test_root = (ROOT / "test").resolve()
    docs: dict[Path, dict] = {}
    cases = []
    for entry in entries:
        if entry.get("family") not in FAMILIES or entry.get("lane") not in LANES:
            raise ValueError("journey requires a known family and lane")
        source = (test_root / str(entry.get("source") or "")).resolve()
        if test_root not in source.parents or not source.is_file():
            raise ValueError("journey source must be a file under test/")
        doc = docs.setdefault(source, yaml.safe_load(source.read_text(encoding="utf-8")))
        if "cases" in doc:
            found = [c for c in doc["cases"] if c.get("id") == entry["id"]]
            case = json.loads(json.dumps(found[0], ensure_ascii=False)) if len(found) == 1 else None
            if case is not None:
                case["source_family"] = case.get("family", "")
        elif "journeys" in doc:
            found = [j for j in doc["journeys"] if j.get("id") == entry["id"]]
            case = _adapt_journey(found[0]) if len(found) == 1 else None
        else:
            raise ValueError("unknown journey source schema")
        if case is None:
            raise ValueError("journey id not found exactly once in its source")
        case.update(family=entry["family"], lane=entry["lane"], source=str(entry["source"]),
                    safety=str(entry.get("safety") or ""), known_red=str(entry.get("known_red") or ""))
        validate_case(case)
        cases.append(case)
    return cases


def freeze(expected_sha: str, provider: str, model: str, corpus: Path = CORPUS, loader=None) -> dict:
    loader = loader or load_cases
    if not re.fullmatch(r"[a-f0-9]{40}", expected_sha):
        raise ValueError("expected release must be a full SHA")
    if _git("status", "--porcelain", "--", *_FREEZE_PATHSPEC):
        raise ValueError("freeze requires committed, clean inputs")
    corpus = corpus.resolve()
    relative_corpus = corpus.relative_to(ROOT).as_posix()
    _git("ls-files", "--error-unmatch", "--", relative_corpus)
    trees = {name: {p: _git("rev-parse", f"{expected_sha}:{p}") for p in paths}
             for name, paths in ASSET_GROUPS.items()}
    return {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "runner_sha": _git("rev-parse", "HEAD"), "release_sha": expected_sha,
        "chat": {"provider": provider, "model": model},
        "decision": {"status": "not_implemented", "model": None, "rubric": None},
        "apk": {"status": "not_measured"},
        "runtime_config": {"status": "not_attested", "note": "no secrets exported"},
        "split": "regression", "corpus_path": relative_corpus,
        "corpus_sha256": digest(loader(corpus)),
        "committed_asset_tree_oids": trees, "assets_sha256": digest(trees),
        "private_manual_package": "approved hash in pinned manual_catalog; no package exported",
        "scopes": list(SCOPES), "vehicle": "v1", "cases": len(loader(corpus)),
        "metrics": ["no_actions", "vehicle_unchanged", "manual_dispatched",
                    "manual_presented", "pending_addressed", "answer_terms",
                    "request_to_final_ms", "actual_model", "provider_usage"],
    }


def judge(expect: dict, obs: dict, detail: dict, *, query: str = "", elapsed_s: float = 0.0) -> dict:
    """Separate planning/dispatch from presentation; a spoken hint is not a card."""
    spans = detail.get("spans") or []
    attrs = [s.get("attrs") or {} for s in spans if isinstance(s, dict)]
    intents = set(str((detail.get("turn") or {}).get("intents") or "").split(","))
    for a in attrs:
        if isinstance(a, dict):
            intents.update(str(a.get("intents") or a.get("intent") or "").split(","))
    # A planner listing an intent is not proof that the corresponding Agent ran.
    manual_dispatched = any(
        isinstance(a, dict) and (a.get("agent_id") or a.get("agent")) == "manual-rag"
        for a in attrs)
    try:
        card = json.loads(obs.get("card_text") or "{}")
    except (TypeError, ValueError):
        card = {}
    def has_manual(c):
        return isinstance(c, dict) and (c.get("type") == "manual" or any(
            has_manual(x) for x in c.get("items", []) if isinstance(x, dict)))
    failures = []
    if obs.get("actions"):
        failures.append("unexpected_action")
    if obs.get("error") or "Agent 内部错误" in str(obs.get("speech") or ""):
        failures.append("technical_failure")
    if expect.get("need_confirm") and not (obs.get("need_confirm") and obs.get("operation_id")):
        failures.append("pending_missing")
    if obs.get("need_confirm") and not expect.get("need_confirm"):
        failures.append("unexpected_confirmation")
    if expect.get("manual"):
        if not manual_dispatched:
            failures.append("manual_not_dispatched")
        if not has_manual(card):
            failures.append("manual_not_presented")
    for term in expect.get("answer_terms") or []:
        if term not in str(obs.get("speech") or ""):
            failures.append("answer_missing:" + term)
    if expect.get("source_evidence"):
        failures.extend(judge_source_evidence(query, obs, detail, card))
    images = {image.get("asset_id") for c in manual_cards(card) for image in c.get("images", [])}
    for asset_id in expect.get("image_assets") or []:
        if asset_id not in images:
            failures.append("manual_image_missing:" + asset_id)
    # 7 月旅程的判据键（`scripts.journey_expect` 唯一实现）；确认挂起仍由上面那条判（它还要求带 operation_id，更严），不重复报
    shared = {k: v for k, v in expect.items() if k in JOURNEY_EXPECT_KEYS and k != "need_confirm"}
    if shared:
        view = _JourneyView({"speech": obs.get("speech") or "", "ui_card": card,
                             "need_confirm": bool(obs.get("need_confirm")), "follow_up": obs.get("follow_up") or ""},
                            list(obs.get("actions") or []), elapsed_s)
        failures.extend("journey:" + f for f in journey_check(shared, view, True, ()))
    return {"failures": failures, "manual_dispatched": manual_dispatched,
            "manual_presented": has_manual(card), "intents": sorted(intents - {""})}


class _JourneyView:
    """喂给 `journey_check` 的一轮观测：v2 收发层不采过程区事件；车况判据只在模拟车车道，这里不给读取函数。"""

    def __init__(self, final: dict, actions: list, elapsed: float) -> None:
        self.final, self.actions, self.process_events, self.elapsed = final, actions, [], elapsed


SOURCE_EVIDENCE = ROOT / "agents/manual_rag/resources/source_evidence.yaml"


def manual_cards(node: dict) -> list[dict]:
    if not isinstance(node, dict):
        return []
    return ([node] if node.get("type") == "manual" else []) + [
        child for item in node.get("items", []) for child in manual_cards(item)]


def judge_source_evidence(query: str, obs: dict, detail: dict, card: dict) -> list[str]:
    """Rebuild pinned source slices; keywords or numeric membership are insufficient."""
    from agents.manual_rag.src.source_evidence import load_evidence_guards
    manual = manual_cards(card)
    if len(manual) != 1 or manual[0].get("_prov", {}).get("mode") != "real":
        return ["source_evidence_card_missing"]
    try:
        item = manual[0]
        pages = {c["page_start"]: c["content"] for c in item.get("chunks", [])}
        guards = load_evidence_guards(SOURCE_EVIDENCE, item.get("document", {}), pages)
        guard = next(g for g in guards if g.matches(query))
        if not guard.available:
            return ["source_evidence_unverifiable"]
        parts = guard.select(query)
        speech = re.sub(r"\s+", "", str(obs.get("speech") or ""))
        failures = ["source_condition_lost:" + part.part_id for part in parts
                    if re.sub(r"\s+", "", part.text.strip()) not in speech]
    except (ValueError, KeyError, TypeError, StopIteration):
        return ["source_evidence_unverifiable"]
    if any(call.get("caller") == "manual-rag" for call in detail.get("llm_calls", [])):
        failures.append("source_evidence_generated")
    return failures


def _redact(value):
    if isinstance(value, dict):
        return {k: (f"[image:{len(v)} chars]" if k == "data_uri" and isinstance(v, str)
                    else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_case(case, repeat, run_id, ws_url, collector, secret, manifest):
    import websockets
    user = f"{run_id}-{case['id'].lower()}-r{repeat}"
    session = user + "-session-1"
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                          scopes=list(SCOPES), timeout_s=1800)
    pending = ""
    rows = []
    baseline = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    if not baseline.settled:
        raise RuntimeError("vehicle baseline did not settle")
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8*1024*1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        turns = list(case["turns"]) + [{"say": "取消", "cancel_pending": True, "cleanup": True}]
        for n, turn in enumerate(turns, 1):
            if turn.get("cancel_pending") and not pending:
                rows.append({"turn": n, "say": turn["say"], "skipped": "no_pending",
                             "cleanup": bool(turn.get("cleanup"))})
                continue
            trace = uuid.uuid4().hex
            start = time.monotonic()
            meta = {"llm_provider": manifest["chat"]["provider"], "llm_model": manifest["chat"]["model"]}
            if case.get("location"):            # 7 月旅程的 setup.location：这次会话的当前位置
                meta.update(current_lat=case["location"]["lat"], current_lng=case["location"]["lng"])
            meta.update(case.get("meta") or {})  # 条目级 meta（校验过：只有语音来源）
            obs = await wire._one_turn(ws, session, turn["say"], trace_id=trace,
                                       operation_id=pending if turn.get("cancel_pending") else "",
                                       meta_overrides=meta)
            elapsed = (time.monotonic()-start)*1000
            detail = await audit._fetch_detail(collector, trace)
            calls = detail.get("llm_calls") or []
            evidence_errors = []
            if (detail.get("turn") or {}).get("trace_id") != trace or detail.get("error"):
                evidence_errors.append("trace_incomplete")
            for call in calls:
                if (call.get("provider"), call.get("model")) != (
                        manifest["chat"]["provider"], manifest["chat"]["model"]):
                    evidence_errors.append("model_drift")
            if obs.get("operation_id"):
                pending = obs["operation_id"]
            if pending in (obs.get("closed_operation_ids") or []):
                pending = ""
            verdict = judge(turn.get("expect") or {}, obs, detail, query=turn["say"], elapsed_s=elapsed / 1000)
            if turn.get("cancel_pending") and pending:
                verdict["failures"].append("pending_not_closed")
            after = await audit._settled_vehicle_state(collector, required_keys=set(baseline.value),
                                                       expected=baseline.value, include_unmanaged=True)
            changed = {k for k in baseline.value.keys() | after.value.keys()
                       if baseline.value.get(k) != after.value.get(k)}
            if not after.settled:
                evidence_errors.append("vehicle_not_settled")
            if changed:
                verdict["failures"].append("vehicle_changed")
            # Store only this synthetic request's trace, never signed URL/token or account config.
            obs = _redact(obs)
            obs["card_text"] = json.dumps(_redact(json.loads(obs.get("card_text") or "{}")), ensure_ascii=False)
            rows.append({"turn": n, "say": turn["say"], "trace_id": trace,
                         "cleanup": bool(turn.get("cleanup")), "observation": obs,
                         "request_to_final_ms": round(elapsed, 2), "verdict": verdict,
                         "evidence_errors": evidence_errors, "vehicle_diff_keys": sorted(changed),
                         "vehicle_before": baseline.value, "vehicle_after": after.value,
                         "trace": _redact(detail)})
            print(f"{case['id']} r{repeat} t{n}: {verdict['failures']} {evidence_errors}", flush=True)
            if obs.get("actions") or changed or evidence_errors:
                # Stop all business sampling. Only cancel a known pending operation;
                # never confirm it or issue inverse vehicle commands to hide a difference.
                if pending:
                    cleanup = await wire._one_turn(
                        ws, session, "取消", operation_id=pending,
                        meta_overrides={"llm_provider": manifest["chat"]["provider"],
                                        "llm_model": manifest["chat"]["model"]})
                    rows[-1]["stop_cleanup"] = cleanup
                    if pending in (cleanup.get("closed_operation_ids") or []):
                        pending = ""
                return {"id": case["id"], "family": case["family"], "repeat": repeat,
                        "session": session, "rows": rows, "open_operation": bool(pending), "stop": True}
    return {"id": case["id"], "family": case["family"], "repeat": repeat,
            "session": session, "rows": rows, "open_operation": bool(pending), "stop": bool(pending)}


def journey_summary(runs: list[dict], repeat: int) -> dict:
    """按旅程汇总（设计 §2）：一次运行全部轮次无业务失败、无证据错误、没被叫停才算过；达标 = 每一次都过。"""
    journeys: dict = {}
    for c in runs:
        ok = not c["stop"] and all(not r["verdict"]["failures"] and not r["evidence_errors"]
                                   for r in c["rows"] if not r.get("skipped"))
        j = journeys.setdefault(c["id"], {"family": c["family"], "runs": 0, "passes": 0})
        j["runs"] += 1
        j["passes"] += int(ok)
    return {"journeys": journeys,
            "journeys_all_pass": sum(1 for j in journeys.values() if j["runs"] == repeat and j["passes"] == repeat)}


def _inputs_unchanged(runner_sha: str) -> bool:
    """跑数期间输入没变：跑数输入在开跑提交与 HEAD 之间没有差异、也没有未提交改动。共享 main 上别的会话提交别处不算变化。"""
    if _git("status", "--porcelain", "--", *_FREEZE_PATHSPEC):
        return False
    return not _git("diff", "--name-only", runner_sha, "HEAD", "--", *_FREEZE_PATHSPEC)


def _skip_reason(case: dict, lanes: set, skip_providers: set) -> str:
    """这条旅程这次为什么不跑（空 = 跑）：车道没开、用到 v2 运行器不支持的原语、依赖的外部服务被点名跳过。都记「未测」。"""
    if case.get("lane", "read_only") not in lanes:
        return "lane:" + case.get("lane", "")
    if case.get("unsupported"):
        return "unsupported:" + ",".join(case["unsupported"])
    blocked = sorted(set(case.get("providers") or []) & skip_providers)
    return ("provider:" + ",".join(blocked)) if blocked else ""


async def run(args):
    manifest_path = getattr(args, "manifest", None)
    corpus = Path(manifest_path or getattr(args, "corpus", CORPUS)).resolve()
    loader = load_manifest if manifest_path else load_cases
    cases = loader(corpus)
    lanes = set(getattr(args, "lane", None) or ["read_only"])
    if lanes - {"read_only"}:
        raise ValueError("write lanes need per-batch authorization and are not implemented yet")
    skip_providers = {x for x in str(getattr(args, "skip_providers", "") or "").split(",") if x}
    if not manifest_path and corpus != CORPUS.resolve():
        from scripts.probe_manual_rag_full_coverage import validate_live_query_safety
        validate_live_query_safety([
            {"id": case["id"], "query": turn["say"]}
            for case in cases for turn in case["turns"] if not turn.get("cancel_pending")
        ])
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
        if {c["id"] for c in cases} != wanted:
            raise ValueError("unknown case ID")
    skipped = [{"id": c["id"], "family": c["family"], "reason": r}
               for c in cases for r in [_skip_reason(c, lanes, skip_providers)] if r]
    cases = [c for c in cases if not _skip_reason(c, lanes, skip_providers)]
    manifest = freeze(args.expected_sha, args.provider, args.model, corpus, loader=loader)
    ws, collector, secret = identity._endpoints()
    payload = {"manifest": manifest, "selected_ids": [c["id"] for c in cases], "skipped": skipped,
               "repeat": args.repeat, "runs": [], "release_start": audit.cloud_release_snapshot(args.expected_sha)}
    out = Path(args.out)
    if out.exists():
        raise ValueError("output already exists; use a new run artifact")
    if payload["release_start"]["failures"]:
        _write(out, payload)
        return 2
    run_id = "e2e-v2-" + uuid.uuid4().hex[:12]
    try:
        for repeat in range(1, args.repeat+1):
            for case in cases:
                result = await run_case(case, repeat, run_id, ws, collector, secret, manifest)
                payload["runs"].append(result)
                _write(out, payload)
                if result["stop"]:
                    return 2
    except Exception as exc:
        # Transport exception strings may contain the signed WS URL. Never emit them.
        payload["probe_error"] = type(exc).__name__
        print("probe stopped: " + type(exc).__name__, flush=True)
        return 2
    finally:
        payload["release_end"] = audit.cloud_release_snapshot(args.expected_sha)
        payload["continuity_errors"] = audit.validate_release_continuity(
            payload["release_start"], payload["release_end"], args.expected_sha)
        payload["runner_unchanged"] = _inputs_unchanged(manifest["runner_sha"])
        rows = [r for c in payload["runs"] for r in c["rows"] if not r.get("skipped")]
        payload["summary"] = {
            "complete_cases": len(payload["runs"]), "expected_cases": len(cases)*args.repeat,
            "measured_turns": len(rows),
            "business_failures": sum(bool(r["verdict"]["failures"]) for r in rows),
            "evidence_failures": sum(bool(r["evidence_errors"]) for r in rows),
            "open_operations": sum(c["open_operation"] for c in payload["runs"]),
            "skipped_journeys": len(payload.get("skipped") or []),
        }
        payload["summary"].update(journey_summary(payload["runs"], args.repeat))
        _write(out, payload)
    return 2 if payload["continuity_errors"] or not payload["runner_unchanged"] else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--expected-sha", default="")
    p.add_argument("--provider", default="minimax")
    p.add_argument("--model", default="MiniMax-M3")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--ids", default="")
    p.add_argument("--corpus", type=Path, default=CORPUS,
                   help="committed regression corpus; custom corpora must pass the read-only question preflight")
    p.add_argument("--manifest", type=Path, default=None,
                   help="core journey manifest (docs/design/2026-10-09-v2-core-journey-freeze.md); overrides --corpus")
    p.add_argument("--lane", action="append", choices=LANES,
                   help="lanes to run (default read_only; write lanes need per-batch authorization)")
    p.add_argument("--skip-providers", default="",
                   help="comma-separated provider keys whose journeys are recorded as not measured (e.g. AMAP_KEY)")
    p.add_argument("--out", default=".artifacts/v2-runtime/baseline.json")
    args = p.parse_args()
    if args.repeat < 1 or args.repeat > 5:
        p.error("repeat must be 1..5")
    if args.dry_run:
        cases = load_manifest(args.manifest) if args.manifest else load_cases(args.corpus)
        print(json.dumps({"cases": cases, "scopes": SCOPES}, ensure_ascii=False, indent=2))
        return 0
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print("baseline preflight failed: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
