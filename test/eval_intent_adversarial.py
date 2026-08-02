#!/usr/bin/env python
"""意图理解与落域对抗测试运行器（发现轨 / 门禁轨，L0–L3）。

本文件只做**参数解析、用例选择与编排**：schema 在 `support/intent_adversarial_contract`，
判定在 `..._judge`，trace 在 `..._trace`，分层运行在 `..._runtime`，指标与 baseline 资格在
`..._report`。业务判断不下放到 CLI——CLI 一旦开始自己判"对不对"，就会出现两套口径。

退出码：
  0 = 选集有效且满足当前 strict 规则
  1 = 语义失败 / 不稳定 / 基线回退 / provider 漂移
  2 = 契约、参数或基础设施错误

用法（离线默认，不连网络、不建 baseline）：
  python test/eval_intent_adversarial.py --suite discovery --lane l0 --list
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
for _p in (str(ROOT), str(ROOT / "gen" / "python"), str(ROOT / "orchestrator" / "edge"),
           str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eval_common  # noqa: E402
import eval_live  # noqa: E402
from support import intent_adversarial_contract as contract  # noqa: E402
from support import intent_adversarial_runtime as runtime  # noqa: E402
from support.intent_adversarial_judge import (  # noqa: E402
    DecisionSnapshot, PlanSnapshot, judge_relation, judge_turn, semantic_signature,
)
from support.intent_adversarial_report import (  # noqa: E402
    AdversarialResult, baseline_eligibility, build_adversarial_report,
    render_adversarial_markdown,
)
from support.intent_adversarial_trace import (  # noqa: E402
    DivergenceEvidence, TraceSink, asset_fingerprint, first_divergence,
)

CORPUS_ROOT = ROOT / "test" / "eval_corpus" / "intent_adversarial"
CASES_DIR = CORPUS_ROOT / "cases"
SUITES_PATH = CORPUS_ROOT / "suites.yaml"
EXEMPTIONS_PATH = CORPUS_ROOT / "coverage_exemptions.yaml"
JOURNEY_LINKS_PATH = CORPUS_ROOT / "journey_links.yaml"
BOUNDARIES_PATH = ROOT / "skills" / "exemplars" / "boundaries.yaml"
JOURNEYS_DIR = ROOT / "test" / "journeys"

DEFAULT_JSON = ROOT / "docs/reviews/eval/_ci-run-intent-adversarial.json"
DEFAULT_MD = ROOT / "docs/reviews/eval/_ci-run-intent-adversarial.md"
FORMAL_BASELINE_JSON = ROOT / "docs/reviews/eval/baseline_intent_adversarial.json"
FORMAL_BASELINE_MD = ROOT / "docs/reviews/eval/baseline_intent_adversarial.md"

LAYERS = ("l0", "l1", "l2", "l3", "all")
LIVE_LAYERS = ("l1", "l2", "l3")
LLM_GATEWAY_HTTP = os.getenv("LLM_GATEWAY_HTTP", "http://localhost:50059")


# ── 参数面 ─────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=("discovery", "gate"), default="discovery")
    ap.add_argument("--layer", "--lane", dest="layer", choices=LAYERS, default="l0")
    ap.add_argument("--case", "--only", dest="cases", action="append", default=[])
    ap.add_argument("--tag", dest="tags", action="append", default=[])
    ap.add_argument("--cohort", choices=("seen_regression", "unseen_transfer"))
    ap.add_argument("--risk", action="append", default=[])
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--provider", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--retrieval-state", choices=("warm", "cold"), default="warm")
    ap.add_argument("--ablations", choices=("off", "on-failure"), default="off")
    ap.add_argument("--repeat", type=int, default=0,
                    help="强制重复次数（诊断用；缺省按 suite 策略）")
    ap.add_argument("--diagnose", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--out-json", default=str(DEFAULT_JSON))
    ap.add_argument("--out-md", default=str(DEFAULT_MD))
    ap.add_argument("--baseline", default=str(FORMAL_BASELINE_JSON))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    return ap.parse_args(argv)


def validate_args(args: argparse.Namespace) -> argparse.Namespace:
    """参数非法一律 SystemExit(2)——把无效组合拼成命令再跑半小时是最贵的失败。"""
    def die(message: str):
        raise SystemExit(f"[intent-adversarial] {message}")

    needs_live = args.layer in LIVE_LAYERS or args.layer == "all"
    if needs_live and not args.live:
        die(f"--layer {args.layer} 需要 --live（L1/L2/L3 必须真实模型）")
    if args.live and (not args.provider or not args.model):
        die("--live 必须同时显式给出 --provider 与 --model（不接受跟随网关默认）")
    if args.retrieval_state == "cold" and args.suite != "discovery":
        die("--retrieval-state cold 只允许用于 discovery（冷启动不进门禁与 baseline）")
    if args.write_baseline:
        if args.suite != "gate":
            die("--write-baseline 只允许 --suite gate")
        if args.layer != "all":
            die("--write-baseline 只允许 --layer all")
        if not args.live or not args.provider or not args.model:
            die("--write-baseline 必须 --live 且显式 --provider/--model")
    if args.repeat and args.repeat < 1:
        die("--repeat 必须 >= 1")
    return args


# ── 选择 ───────────────────────────────────────────────────────────────────


def _tag_matches(case, wanted: str) -> bool:
    """一个 `--tag` 既可命中 list/scalar 的值，也可命中值为真的 tag key。"""
    for key, value in (case.tags or {}).items():
        if key == wanted and value is True:
            return True
        if isinstance(value, (list, tuple, set)):
            if wanted in {str(v) for v in value}:
                return True
        elif str(value) == wanted:
            return True
    return False


def select_cases(cases: list, args: argparse.Namespace,
                 suite: contract.SuiteConfig) -> list:
    live = args.layer in LIVE_LAYERS or args.layer == "all"
    statuses = set(suite.live_statuses if live else suite.statuses)
    out = []
    for case in cases:
        if case.status not in statuses:
            continue
        if args.cases and case.id not in set(args.cases):
            continue
        if args.tags and not all(_tag_matches(case, tag) for tag in args.tags):
            continue
        if args.cohort and case.cohort != args.cohort:
            continue
        if args.risk and case.risk not in set(args.risk):
            continue
        out.append(case)
    return out


def layers_for(case, requested: str) -> tuple[str, ...]:
    declared = tuple(str(v) for v in (case.tags.get("layers") or []))
    if requested == "all":
        return declared
    return (requested,) if requested in declared else ()


# ── L0 ─────────────────────────────────────────────────────────────────────


def _l0_expectation(expected):
    """L0 只断言它自己能定的部分：ingress、检索资产、确认前副作用。

    计划正确与否要模型才知道；把 plan 断言也放进 L0，只会得到一个恒红或恒绿的层。
    """
    return replace(expected,
                   addressed=None, decision_allowed=(), clarify="allowed",
                   plan=replace(expected.plan, assert_plan=False),
                   replans=())


def run_l0_case(case) -> tuple[DecisionSnapshot, Any]:
    turn = case.turns[0]
    edge = runtime.run_edge_turn(
        turn.utterance,
        cloud_need_confirm=bool(turn.expected.no_side_effect_before_confirm))
    retrieval = runtime.run_retrieval_turn(turn.utterance)
    snapshot = DecisionSnapshot(
        ingress=edge.ingress, addressed=True,
        decision="execute", clarify=False,
        plan=PlanSnapshot(steps=(), complexity="", goal="",
                          skills=retrieval.skills, exemplars=retrieval.exemplars,
                          hint_effect="", catalog_stats={}),
        side_effects=tuple(edge.side_effects))
    return snapshot, judge_turn(_l0_expectation(turn.expected), snapshot)


# ── L1 / L2 ────────────────────────────────────────────────────────────────


def _l1_expectation(expected):
    """L1 不判 ingress：PlanBuilder 按定义就在云侧，那条断言在这一层恒真。"""
    return replace(expected, ingress_allowed=(), ingress_forbidden=())


async def run_l1_case(case, agents, builder):
    turn = case.turns[0]
    snapshot = await runtime.run_planner_turn(turn, agents, builder)
    return snapshot, judge_turn(_l1_expectation(turn.expected), snapshot)


async def run_l2_case(case, agents, builder, confirm_intents):
    turn = case.turns[0]
    sink = TraceSink()
    clients = runtime.SafeClients(
        agents,
        history=list(turn.context.get("history") or []),
        memories=list(turn.context.get("memories") or []),
        confirm_intents=confirm_intents)
    harness = runtime.build_engine_harness(builder, agents, clients, sink)
    focus = turn.context.get("focus") or {}
    session_id = f"adv-{case.id}"
    if focus:
        harness.seed_focus(session_id, focus)
    pending = turn.context.get("pending_confirm") or {}
    if pending:
        harness.seed_pending_confirm(session_id, intent=str(pending.get("intent") or ""),
                                     step_id=str(pending.get("step_id") or "s1"))
    edge, engine = runtime.run_full_entry_turn(
        turn.utterance, harness, session_id=session_id,
        meta={k: str(v) for k, v in (turn.context.get("meta") or {}).items()})
    plans = [trace.plan for trace in sink.plans]
    snapshot = DecisionSnapshot(
        ingress=edge.ingress,
        addressed=True,
        decision=(engine.decision if engine else "execute"),
        clarify=bool(engine and engine.decision == "clarify"),
        plan=plans[0] if plans else PlanSnapshot.empty(),
        replans=tuple(plans[1:]),
        side_effects=tuple({"intent": row["intent"]} for row in
                           (engine.side_effects if engine else ())))
    return snapshot, judge_turn(turn.expected, snapshot)


# ── L3 ─────────────────────────────────────────────────────────────────────


def load_journey_links(path: Path = JOURNEY_LINKS_PATH) -> dict[str, list[str]]:
    if not Path(path).is_file():
        return {}
    data = contract._read_yaml(Path(path))
    contract._expect_keys(data, {"schema_version", "links"}, str(path))
    if data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version must be 1")
    known = known_journey_ids()
    out: dict[str, list[str]] = {}
    for case_id, journey_ids in (data.get("links") or {}).items():
        ids = [str(j) for j in (journey_ids or [])]
        missing = [j for j in ids if j not in known]
        if missing:
            raise ValueError(f"{path}: case {case_id} links unknown journeys {missing}")
        out[str(case_id)] = ids
    return out


def known_journey_ids(directory: Path = JOURNEYS_DIR) -> set[str]:
    ids: set[str] = set()
    for path in sorted(Path(directory).glob("**/*.yaml")):
        data = contract._read_yaml(path)
        for row in (data.get("journeys") or data.get("cases") or []):
            if isinstance(row, dict) and row.get("id"):
                ids.add(str(row["id"]))
    return ids


def run_l3(journey_ids: list[str], *, provider: str, model: str,
           artifact_root: Path | None = None, env: dict[str, str] | None = None) -> int:
    """复用现有 runner。`--id` 与 `--full` 在当前 runner 里互斥，别拼成无效命令。"""
    child_env = dict(env or os.environ)
    child_env["E2E_JOURNEY_IDS"] = ",".join(journey_ids)
    if artifact_root is not None:
        Path(artifact_root).mkdir(parents=True, exist_ok=True)
        for key in ("TMPDIR", "TEMP", "TMP"):
            child_env[key] = str(artifact_root)
    argv = [sys.executable, "scripts/run_e2e.py", "--id", "e2e_journeys",
            "--provider", provider, "--model", model]
    proc = subprocess.run(argv, cwd=str(ROOT), env=child_env, check=False)
    return proc.returncode


def read_l3_report(artifact_root: Path) -> dict[str, str]:
    """从 runner 产出的结构化 journeys report 读逐条状态；只看退出码等于没读。"""
    statuses: dict[str, str] = {}
    for path in sorted(Path(artifact_root).glob("**/journeys_report.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in data.get("journeys") or []:
            if isinstance(row, dict) and row.get("id"):
                statuses[str(row["id"])] = str(row.get("status") or "")
    return statuses


# ── 结果装配 ───────────────────────────────────────────────────────────────


def _dimensions(case, layer: str, snapshot: DecisionSnapshot,
                provider_model: str) -> dict[str, tuple[str, ...]]:
    intents = tuple(snapshot.plan.intents)
    return {
        "intent": intents,
        "domain": tuple(sorted({i.split(".", 1)[0] for i in intents})
                        or tuple(str(d) for d in (case.tags.get("domains") or []))),
        "boundary": tuple(filter(None, (str(case.tags.get("boundary") or ""),))),
        "attack": tuple(str(a) for a in (case.tags.get("attacks") or [])),
        "risk": (case.risk,),
        "ingress": (snapshot.ingress,),
        "cohort": (case.cohort,),
        "layer": (layer,),
        "provider": (provider_model,),
        "status": (case.status,),
        "provenance": (str(case.provenance.get("kind") or "authored"),),
    }


def _metrics_for(case, judgement, snapshot: DecisionSnapshot) -> dict[str, float]:
    metrics = {"exact_plan_set": 1.0 if judgement.passed else 0.0}
    for key in ("required_group_recall", "forbidden_route_count", "overroute_count",
                "dependency_pass"):
        if key in judgement.metrics:
            metrics[key] = judgement.metrics[key]
    named = {a.name: a.passed for a in judgement.assertions}
    for name, key in (("ingress_allowed", "ingress_pass"),
                      ("ingress_forbidden", "ingress_pass")):
        if name in named:
            metrics[key] = 1.0 if named[name] else 0.0
    turn = case.turns[0]
    if "clarify" in named:
        side = ("clarify_required_pass" if turn.expected.clarify == "required"
                else "clarify_forbidden_pass")
        metrics[side] = 1.0 if named["clarify"] else 0.0
    if not turn.expected.plan.assert_plan:
        metrics.pop("exact_plan_set", None) if not judgement.assertions else None
    return metrics


def _dangerous(case, judgement, snapshot: DecisionSnapshot) -> bool:
    if snapshot.side_effects and case.turns[0].expected.no_side_effect_before_confirm:
        return True
    forbidden = judgement.metrics.get("forbidden_route_count", 0.0)
    return bool(forbidden) and case.risk in {"high", "critical"}


def _assertion_rows(judgement) -> tuple[dict[str, Any], ...]:
    return tuple({"name": a.name, "passed": a.passed, "expected": repr(a.expected),
                  "actual": repr(a.actual)} for a in judgement.assertions)


def _snapshot_dict(snapshot: DecisionSnapshot) -> dict[str, Any]:
    return {
        "ingress": snapshot.ingress, "addressed": snapshot.addressed,
        "decision": snapshot.decision, "clarify": snapshot.clarify,
        "goal": snapshot.plan.goal, "complexity": snapshot.plan.complexity,
        "steps": [{"id": s.id, "intent": s.intent, "slots": s.slots,
                   "depends_on": list(s.depends_on), "slot_refs": s.slot_refs}
                  for s in snapshot.plan.steps],
        "replans": [[s.intent for s in plan.steps] for plan in snapshot.replans],
        "skills": list(snapshot.plan.skills), "exemplars": list(snapshot.plan.exemplars),
        "hint_effect": snapshot.plan.hint_effect,
        "catalog_stats": snapshot.plan.catalog_stats,
        "side_effects": list(snapshot.side_effects),
    }


def _expected_dict(case) -> dict[str, Any]:
    turn = case.turns[0]
    plan = turn.expected.plan
    return {
        "utterance": turn.utterance,
        "ingress_allowed": list(turn.expected.ingress_allowed),
        "ingress_forbidden": list(turn.expected.ingress_forbidden),
        "decision_allowed": list(turn.expected.decision_allowed),
        "clarify": turn.expected.clarify,
        "required_intent_groups": [list(g.any_of) for g in plan.required_groups],
        "forbidden_intents": list(plan.forbidden_intents),
        "allow_extra_intents": plan.allow_extra_intents,
        "relation": (None if not case.relation else
                     {"base_case": case.relation.base_case, "type": case.relation.type,
                      "expectation": case.relation.expectation}),
        "repro": f"python test/eval_intent_adversarial.py --case {case.id} "
                 f"--layer {'/'.join(str(v) for v in (case.tags.get('layers') or []))} "
                 f"--repeat 3 --diagnose",
    }


# ── baseline 写入 ─────────────────────────────────────────────────────────


def write_baseline_if_eligible(report: dict, markdown: str, eligibility,
                               formal_json: Path, formal_md: Path,
                               rejected_json: Path, rejected_md: Path) -> bool:
    """不合资格时**一个字节都不碰**正式基线，诊断另写 ignored 文件。"""
    if not eligibility.eligible:
        eval_common.write_report(report, markdown, Path(rejected_json), Path(rejected_md))
        return False
    eval_common.write_report(report, markdown, Path(formal_json), Path(formal_md))
    return True


# ── main ───────────────────────────────────────────────────────────────────


def _semantic_retrieval_expected() -> bool:
    from orchestrator.cloud import exemplars as _exemplars
    return (_exemplars.mode() != "off"
            and _exemplars.retrieval_mode() == "hybrid")


def _worktree_clean(ignore: set[str]) -> bool:
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        path = line[3:].strip()
        if path and path not in ignore:
            return False
    return True


def _print_selection(cases, suite, args, contract_errors) -> int:
    if args.layer == "l3":
        links = load_journey_links()
        ids = sorted({j for case in cases for j in links.get(case.id, [])})
        if not ids:
            print("no linked journeys for the current selection", file=sys.stderr)
            return 2
        print(",".join(ids))
        return 0
    print(f"suite={args.suite} layer={args.layer} selected={len(cases)}")
    by_status: dict[str, int] = {}
    by_attack: dict[str, int] = {}
    for case in cases:
        by_status[case.status] = by_status.get(case.status, 0) + 1
        for attack in (case.tags.get("attacks") or []):
            by_attack[str(attack)] = by_attack.get(str(attack), 0) + 1
    print("status:", json.dumps(by_status, ensure_ascii=False, sort_keys=True))
    print("attacks:", json.dumps(by_attack, ensure_ascii=False, sort_keys=True))
    print(f"suite bounds: [{suite.min_cases}, {suite.max_cases}]")
    for row in contract_errors:
        print(f"  gap: {row}")
    return 2 if (contract_errors and (args.strict or args.write_baseline)) else 0


def _gather_contract_errors(cases, suite, args) -> tuple[list[str], list[str]]:
    """硬错误（永远阻断）与缺口（普通 --list 只展示，strict 才升级）分开返回。"""
    active = eval_live.known_intents()
    hard = contract.validate_cases(cases, active)
    exemptions = contract.load_coverage_exemptions(EXEMPTIONS_PATH, active)
    skills, exemplars = runtime.skill_and_exemplar_inventory()
    hard += contract.validate_retrieval_references(cases, skills, exemplars)
    ledger = contract.load_boundary_ledger(BOUNDARIES_PATH)
    gaps = contract.validate_coverage(cases, active, exemptions)
    gaps += contract.validate_boundary_coverage(cases, ledger)
    gaps += contract.validate_suite_counts(cases, suite)
    gaps += contract.validate_gate_candidate_count(cases)
    return hard, gaps


def main(argv: list[str] | None = None) -> int:
    args = validate_args(parse_args(argv))
    try:
        all_cases = contract.load_cases(CASES_DIR)
        suites = contract.load_suites(SUITES_PATH)
    except (ValueError, OSError) as exc:
        print(f"[intent-adversarial] contract error: {exc}", file=sys.stderr)
        return 2
    suite = suites[args.suite]
    selected = select_cases(all_cases, args, suite)
    try:
        hard_errors, gaps = _gather_contract_errors(all_cases, suite, args)
    except (ValueError, OSError) as exc:
        print(f"[intent-adversarial] contract error: {exc}", file=sys.stderr)
        return 2
    if hard_errors:
        for row in hard_errors[:50]:
            print(f"[contract] {row}", file=sys.stderr)
        return 2
    if args.list:
        return _print_selection(selected, suite, args, gaps)

    lock = None
    infrastructure_errors: list[str] = []
    results: list[AdversarialResult] = []
    provider_model = "deterministic"
    warmed = 0
    try:
        agents = eval_live.load_agents(include_edge=True)
        confirm_intents = runtime.confirm_intent_inventory(agents)
        builder = None
        if args.live:
            lock = eval_common.ProviderLock(LLM_GATEWAY_HTTP, want=args.provider,
                                            model=args.model)
            provider_model = lock.pin()
            builder = eval_live.make_builder("intent-adversarial", args.temperature,
                                             timeout=args.timeout, model="")
            if args.retrieval_state == "warm":
                warmed = asyncio.run(eval_live.warm_exemplars())
                # 预热返回 0 有两种可能：范例层本来就关着（合法），或者 Embed 打不通
                # 被静默降级成纯词法（不合法——那样整轮 L1 测的根本不是生产装配）。
                # 后者必须是基础设施错误：一次「悄悄只跑了词法档」的发现轨会污染
                # 之后所有关于知识层的结论。
                if not warmed and _semantic_retrieval_expected():
                    infrastructure_errors.append(
                        "exemplar_warmup_failed: 语义检索档位为 hybrid 但预热 0 条"
                        "（多半是 LLM_GATEWAY_ADDR 未指向可达网关，Embed 被降级）")
        results, infra = _execute(selected, args, suite, agents, builder,
                                  confirm_intents, provider_model, lock)
        infrastructure_errors.extend(infra)
    except RuntimeError as exc:
        print(f"[intent-adversarial] infrastructure error: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.restore()

    l3_selected, l3_statuses = _l3_evidence(selected, args, provider_model)
    meta = {
        "suite": args.suite, "layer": args.layer,
        "retrieval_state": args.retrieval_state, "warmed_exemplars": warmed,
        "provider_locked": bool(lock and lock.locked),
        "provider_drift": bool(lock and lock.drifts),
        "provider_lock": (lock.summary() if lock else {}),
        "provider_model": provider_model,
        "code_sha": eval_common.git_short_sha(),
        "worktree_clean": _worktree_clean({
            os.path.relpath(args.out_json, ROOT).replace("\\", "/"),
            os.path.relpath(args.out_md, ROOT).replace("\\", "/")}),
        "assets": asset_fingerprint(ROOT),
        "infrastructure_errors": infrastructure_errors,
        "selected_statuses": sorted({case.status for case in selected}),
        "coverage_gaps": gaps,
        "l3_selected": l3_selected,
        "l3_complete": bool(l3_selected) and all(
            l3_statuses.get(j) == "pass" for j in l3_selected),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["assets_complete"] = bool(meta["assets"].get("complete"))
    expected_units = {f"{case.id}@{layer}" for case in selected
                      for layer in layers_for(case, args.layer)}
    meta["case_set_complete"] = expected_units <= {r.result_id for r in results}
    baseline = eval_common.load_baseline(Path(args.baseline))
    report = build_adversarial_report(results, meta)
    if baseline:
        diff = eval_common.diff_against_baseline(report, baseline)
        report["meta"]["baseline_regressions"] = [cid for cid, _, _ in diff.regressions]
    else:
        report["meta"]["baseline_regressions"] = []
    markdown = render_adversarial_markdown(report)

    eligibility = baseline_eligibility(report)
    if args.write_baseline:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        written = write_baseline_if_eligible(
            report, markdown, eligibility, FORMAL_BASELINE_JSON, FORMAL_BASELINE_MD,
            ROOT / f"docs/reviews/eval/_ci-run-intent-adversarial-rejected-{stamp}.json",
            ROOT / f"docs/reviews/eval/_ci-run-intent-adversarial-rejected-{stamp}.md")
        if not written:
            for reason in eligibility.reasons:
                print(f"[baseline rejected] {reason}", file=sys.stderr)
            return 2
    else:
        eval_common.write_report(report, markdown, Path(args.out_json),
                                 Path(args.out_md))

    _print_summary(report)
    if infrastructure_errors:
        return 2
    if gaps and (args.strict or args.write_baseline):
        for row in gaps[:50]:
            print(f"[coverage] {row}", file=sys.stderr)
        return 2
    failed = report["overall"]["passed"] != report["overall"]["total"]
    if failed or report["meta"]["baseline_regressions"] or meta["provider_drift"]:
        return 1
    return 0


def _print_summary(report: dict) -> None:
    metrics = report["metrics"]
    print(f"cases={report['overall']['total']} passed={report['overall']['passed']}")
    for name in ("exact_plan_set_rate", "required_group_recall",
                 "forbidden_route_rate", "capability_hallucination_rate",
                 "instability_rate"):
        row = metrics.get(name) or {}
        value = row.get("value")
        print(f"  {name}: {'null' if value is None else f'{value * 100:.1f}%'}"
              f" ({row.get('numerator', 0):g}/{row.get('denominator', 0):g})")
    for row in (report.get("weakest") or [])[:5]:
        print(f"  weakest {row['dimension']}={row['cell']} "
              f"{row['pass_rate'] * 100:.1f}% (n={row['total']})")


def _l3_evidence(selected, args, provider_model) -> tuple[list[str], dict[str, str]]:
    if args.layer not in {"l3", "all"} or not args.live:
        return [], {}
    links = load_journey_links()
    ids = sorted({j for case in selected
                  if "l3" in layers_for(case, args.layer) for j in links.get(case.id, [])})
    if not ids:
        return [], {}
    artifact_root = ROOT / "docs" / "reviews" / "eval" / "_ci-run-intent-l3-artifacts"
    code = run_l3(ids, provider=args.provider, model=args.model,
                  artifact_root=artifact_root)
    statuses = read_l3_report(artifact_root)
    if code != 0 and not statuses:
        statuses = {j: "fail" for j in ids}
    return ids, statuses


def _execute(selected, args, suite, agents, builder, confirm_intents,
             provider_model, lock) -> tuple[list[AdversarialResult], list[str]]:
    results: list[AdversarialResult] = []
    infra: list[str] = []
    by_id = {case.id: case for case in selected}
    signatures: dict[str, Any] = {}
    checked = 0
    for case in selected:
        for layer in layers_for(case, args.layer):
            if layer == "l3":
                continue                       # L3 证据由 _l3_evidence 回填
            try:
                outcome = _run_case_layer(case, layer, args, suite, agents, builder,
                                          confirm_intents, provider_model)
            except RuntimeError as exc:
                infra.append(f"{case.id}@{layer}: {exc}")
                continue
            results.append(outcome[0])
            signatures[f"{case.id}@{layer}"] = outcome[1]
            checked += 1
            if lock is not None and checked % 20 == 0:
                lock.check(f"{case.id}@{layer}")
    if lock is not None:
        lock.check("final")
    _apply_relations(results, by_id, signatures, args)
    return results, infra


def _apply_relations(results, by_id, signatures, args) -> None:
    """relation 只在 base 与 variant 的 absolute 判定都算完之后才裁——
    「与另一个输出相同」不能替代绝对 gold，否则两个一起错也是绿的。"""
    index = {r.result_id: i for i, r in enumerate(results)}
    for case in by_id.values():
        if not case.relation:
            continue
        for layer in layers_for(case, args.layer):
            variant_key = f"{case.id}@{layer}"
            base_key = f"{case.relation.base_case}@{layer}"
            if variant_key not in signatures or base_key not in signatures:
                continue
            judgement = judge_relation(case.relation, signatures[base_key],
                                       signatures[variant_key])
            row = results[index[variant_key]]
            metrics = dict(row.metrics)
            metrics["relation_pass"] = judgement.metrics["relation_pass"]
            if case.relation.type == "context_override":
                metrics["context_override_pass"] = judgement.metrics["relation_pass"]
            results[index[variant_key]] = replace(
                row, metrics=metrics, passed=row.passed and judgement.passed,
                assertions=row.assertions + _assertion_rows(judgement))


def _run_case_layer(case, layer, args, suite, agents, builder, confirm_intents,
                    provider_model):
    risk = case.risk
    repeats = args.repeat or (suite.high_risk_repeats
                              if risk in {"high", "critical"} else 1)
    outcomes: list[runtime.RepeatOutcome] = []
    snapshots: list[DecisionSnapshot] = []
    judgements = []

    def once():
        if layer == "l0":
            return run_l0_case(case)
        if builder is None:
            raise RuntimeError(f"layer {layer} 需要 live builder")
        if layer == "l1":
            return asyncio.run(run_l1_case(case, agents, builder))
        return asyncio.run(run_l2_case(case, agents, builder, confirm_intents))

    for _ in range(repeats):
        snapshot, judgement = once()
        snapshots.append(snapshot)
        judgements.append(judgement)
        outcomes.append(runtime.RepeatOutcome(
            passed=judgement.passed, signature=repr(semantic_signature(snapshot)),
            dangerous=_dangerous(case, judgement, snapshot)))
    if repeats == 1 and not outcomes[0].passed and layer != "l0":
        for _ in range(suite.failure_repeats - 1):
            snapshot, judgement = once()
            snapshots.append(snapshot)
            judgements.append(judgement)
            outcomes.append(runtime.RepeatOutcome(
                passed=judgement.passed, signature=repr(semantic_signature(snapshot)),
                dangerous=_dangerous(case, judgement, snapshot)))
    classification = runtime.classify_repeats(outcomes, risk,
                                              deterministic=layer == "l0")
    judgement = judgements[0]
    snapshot = snapshots[0]
    ablations = _run_ablations(case, layer, args, classification, agents, builder)
    divergence = "" if classification.status == "pass" else first_divergence(
        DivergenceEvidence(
            full_entry_pass=classification.status == "pass",
            empty_history_pass=_arm_passed(ablations, "empty-history"),
            retrieval_ablation_pass=(_arm_passed(ablations, "no-skills")
                                     or _arm_passed(ablations, "no-exemplars")),
            pre_hint_pass=_arm_passed(ablations, "no-hints")))
    result = AdversarialResult(
        result_id=f"{case.id}@{layer}", case_id=case.id, layer=layer,
        title=case.title, passed=classification.status == "pass",
        repeat_status=classification.status, cohort=case.cohort, risk=risk,
        status=case.status, provenance_kind=str(case.provenance.get("kind") or ""),
        provider_model=provider_model if layer != "l0" else "deterministic",
        dimensions=_dimensions(case, layer, snapshot,
                               provider_model if layer != "l0" else "deterministic"),
        metrics=_metrics_for(case, judgement, snapshot),
        expected=_expected_dict(case), actual=_snapshot_dict(snapshot),
        # 准入清单按本案例的 catalog 条件算：A8 把某个能力从 catalog 拿掉后，
        # 计划里还出现它就是能力幻觉——用全量 inventory 当分母会看不见这件事。
        admitted_intents=_admitted_intents(case, agents),
        actual_intents=tuple(snapshot.plan.intents),
        assertions=_assertion_rows(judgement),
        repetitions=tuple({"passed": o.passed, "signature": o.signature[:400],
                           "dangerous": o.dangerous} for o in classification.outcomes),
        divergence=divergence, ablations=tuple(ablations))
    return result, snapshot


def _admitted_intents(case, agents) -> tuple[str, ...]:
    catalog = runtime.filter_unavailable_capabilities(
        agents, set(case.turns[0].context.get("unavailable_intents") or []))
    return tuple(sorted(
        str(cap.intent) for agent in catalog
        for cap in (getattr(agent.manifest, "capabilities", None) or [])
        if getattr(cap, "intent", "")))


def _arm_passed(ablations: list[dict], arm: str) -> bool:
    return any(row["arm"] == arm and row["status"] == "pass" for row in ablations)


def _run_ablations(case, layer, args, classification, agents, builder) -> list[dict]:
    if args.ablations != "on-failure" or layer == "l0" or builder is None:
        return []
    arms = runtime.requested_ablations(classification.status)
    rows: list[dict] = []
    for arm in arms:
        if arm == "cloud-direct":
            continue                        # 完整入口对照由 L2 自身提供
        try:
            status = _run_single_ablation(case, layer, arm, args, agents)
        except RuntimeError as exc:
            rows.append({"arm": arm, "status": "error", "detail": str(exc),
                         "causal": "invalid"})
            continue
        rows.append({"arm": arm, "status": status,
                     "causal": runtime.causal_effect(classification.status, status,
                                                     True, True)})
    return rows


def _run_single_ablation(case, layer, arm, args, agents) -> str:
    turn = replace(case.turns[0],
                   context=runtime.ablation_context(arm, case.turns[0].context))
    ablated_case = replace(case, turns=(turn,))
    outcomes = []
    with runtime.temporary_env(runtime.ablation_env(arm)):
        for _ in range(3):
            arm_builder = eval_live.make_builder("intent-adversarial-ablation",
                                                 args.temperature, timeout=args.timeout)
            if arm == "no-hints":
                runtime.disable_route_hints(arm_builder)
            snapshot, judgement = asyncio.run(
                run_l1_case(ablated_case, agents, arm_builder))
            outcomes.append(runtime.RepeatOutcome(
                passed=judgement.passed, signature=repr(semantic_signature(snapshot))))
    return runtime.classify_repeats(outcomes, case.risk).status


if __name__ == "__main__":
    raise SystemExit(main())
