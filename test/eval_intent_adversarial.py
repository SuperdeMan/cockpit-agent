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
import uuid
from dataclasses import dataclass, field, replace
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
    DecisionSnapshot, PlanSnapshot, TurnJudgement, judge_plan, judge_relation,
    judge_turn, semantic_signature,
)
from support.intent_adversarial_report import (  # noqa: E402
    AdversarialResult, baseline_eligibility, build_adversarial_report,
    render_adversarial_markdown,
)
from support.intent_adversarial_trace import (  # noqa: E402
    DivergenceEvidence, TraceSink, asset_fingerprint, deterministic_divergence,
    divergence_candidates, first_divergence, probe_builder,
)
from support.intent_adversarial_trace import (  # noqa: E402
    evidence_dict as trace_evidence_dict,
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


@dataclass(frozen=True)
class TurnOutcome:
    """**一轮**的观测与判定。多轮 case 的每一轮都要有自己的这一份。

    契约一直接受 `turns` 多轮、coverage 也按全部轮记账，但三层运行器都固定读
    `case.turns[0]`——后续轮既没被执行、又把覆盖缺口涂绿了。
    """
    snapshot: DecisionSnapshot
    judgement: Any
    raw_intents: tuple[str, ...] = ()
    raw_observed: bool = False
    raw_planner_pass: bool | None = None
    pre_hint_pass: bool | None = None


@dataclass
class UnitRuns:
    """一个 `case@layer` 的全部重复运行；每次运行是一串逐轮 outcome。"""
    case: Any
    layer: str
    runs: list[list[TurnOutcome]] = field(default_factory=list)

    def turn_count(self) -> int:
        return min((len(run) for run in self.runs), default=0)


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


SELECTION_FILTERS = ("cases", "tags", "cohort", "risk")


def selection_filters(args: argparse.Namespace) -> list[str]:
    """当前生效的选集过滤器名单。空 = 跑完整声明集。"""
    active = []
    for name in SELECTION_FILTERS:
        value = getattr(args, name, None)
        if value:
            active.append(f"--{'case' if name == 'cases' else name.rstrip('s')}")
    return active


def validate_args(args: argparse.Namespace) -> argparse.Namespace:
    """参数非法一律退出码 2——把无效组合拼成命令再跑半小时是最贵的失败。

    `SystemExit(<字符串>)` 的进程码是 **1**，而模块头声明参数/契约错误是 2：
    自动化于是把「命令根本无效」记成「产品语义失败」。退出码必须显式给。
    """
    def die(message: str):
        print(f"[intent-adversarial] {message}", file=sys.stderr)
        raise SystemExit(2)

    # `--list` 只做选择与缺口展示，不跑任何模型——它是拿 journey id 的正常入口
    # （Task 17 Step 3），要求它 --live 会把这条路堵死。
    needs_live = (args.layer in LIVE_LAYERS or args.layer == "all") and not args.list
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
        # **选择过滤器就是等价的 `--force`。** CLI 里没有绕过参数，但
        # `--write-baseline --case only.one --repeat 1` 一样能用「一条全绿的用例」
        # 盖掉正式基线：`case_set_complete` 只比较「当前选集跑齐没有」，不证明
        # 「选集等于完整 stable 声明集」。这里直接堵在参数面。
        active = selection_filters(args)
        if active:
            die(f"--write-baseline 不接受选集过滤器（{', '.join(active)}）："
                "正式基线必须是完整 stable 声明集的一次完整运行")
        if args.repeat:
            die("--write-baseline 不接受 --repeat：重复策略由 suite 定，"
                "--repeat 1 会把高风险三次策略降成一次")
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
    return _with_relation_bases(out, cases, statuses)


def _with_relation_bases(selected: list, all_cases: list, statuses: set) -> list:
    """选中 variant 就必须把它的 base 一起带上。

    relation 是**成对**判定：只选 variant 时 base 的签名不存在，`_apply_relations`
    会静默跳过——于是「`--case <variant>` 复现一条 relation 失败」永远复现不出来，
    而报告里那条 case 明明写着 relation 断言失败。
    """
    by_id = {case.id: case for case in all_cases}
    picked = {case.id: case for case in selected}
    for case in list(selected):
        base_id = case.relation.base_case if case.relation else ""
        base = by_id.get(base_id)
        if base is not None and base.id not in picked and base.status in statuses:
            picked[base.id] = base
    order = {case.id: index for index, case in enumerate(all_cases)}
    return sorted(picked.values(), key=lambda case: order.get(case.id, 0))


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


def run_l0_case(case) -> list["TurnOutcome"]:
    """逐轮跑完整条 case，**同一个 Edge 会话**——VAL 状态与端侧会话态才连得上。"""
    session = runtime.EdgeSession(
        cloud_need_confirm=any(turn.expected.no_side_effect_before_confirm
                               for turn in case.turns))
    outcomes: list[TurnOutcome] = []
    for turn in case.turns:
        edge = session.turn(turn.utterance,
                            is_confirmation=bool(turn.context.get("is_confirmation")))
        retrieval = runtime.run_retrieval_turn(turn.utterance)
        snapshot = DecisionSnapshot(
            ingress=edge.ingress, addressed=True,
            decision="execute", clarify=False,
            plan=PlanSnapshot(steps=(), complexity="", goal="",
                              skills=retrieval.skills, exemplars=retrieval.exemplars,
                              hint_effect="", catalog_stats={}),
            side_effects=runtime.edge_side_effect_rows(edge))
        outcomes.append(TurnOutcome(
            snapshot=snapshot,
            judgement=judge_turn(_l0_expectation(turn.expected), snapshot)))
    return outcomes


# ── L1 / L2 ────────────────────────────────────────────────────────────────


def _l1_expectation(expected):
    """L1 不判 ingress：PlanBuilder 按定义就在云侧，那条断言在这一层恒真。"""
    return replace(expected, ingress_allowed=(), ingress_forbidden=())


async def run_l1_case(case, agents, builder) -> list["TurnOutcome"]:
    """逐轮跑 Planner，history 按轮累积；每轮都留 raw / pre-hint 证据。"""
    sink = TraceSink()
    outcomes: list[TurnOutcome] = []
    history: list[dict] = []
    with probe_builder(builder, sink):
        for turn in case.turns:
            context = dict(turn.context)
            if history:
                context["history"] = list(context.get("history") or []) + history
            probed = replace(turn, context=context)
            before = (len(sink.validations), len(sink.hints))
            snapshot = await runtime.run_planner_turn(probed, agents, builder)
            _reject_unreached_planner(case, snapshot)
            outcomes.append(_turn_outcome(
                turn, snapshot, judge_turn(_l1_expectation(turn.expected), snapshot),
                sink, before))
            history += [{"role": "user", "text": turn.utterance},
                        {"role": "assistant", "text": snapshot.plan.goal or "已处理"}]
    return outcomes


def _turn_outcome(turn, snapshot, judgement, sink: TraceSink,
                  before: tuple[int, int]) -> "TurnOutcome":
    """把本轮新增的 trace 折成证据：校验前候选、Hint 前计划。

    这两份证据原来只活在单测里，主入口从不消费——于是「每个 live 失败都有首偏离点」
    退化成「一律记 PLANNER_DIVERGENCE」。判定用的是**同一个 judge_plan**，不另立口径。
    """
    validations = sink.validations[before[0]:]
    hints = sink.hints[before[1]:]
    assert_plan = turn.expected.plan.assert_plan
    raw = validations[-1] if validations else None
    hint = hints[-1] if hints else None
    return TurnOutcome(
        snapshot=snapshot, judgement=judgement,
        raw_intents=tuple(raw.raw_intents) if raw else (),
        raw_observed=raw is not None,
        raw_planner_pass=(_plan_passes(turn.expected.plan, raw.raw_candidate)
                          if raw and assert_plan else None),
        pre_hint_pass=(_plan_passes(turn.expected.plan, hint.before)
                       if hint and assert_plan else None))


def _plan_passes(expectation, plan: PlanSnapshot) -> bool:
    out = TurnJudgement()
    judge_plan(expectation, plan, out)
    return out.passed


def _reject_unreached_planner(case, snapshot) -> None:
    """模型压根没被够着 → 基础设施错误，不许算成产品失败。

    2026-08-03 实测：一次全量发现轨中途撞上网关 `all models failed`，`_fallback` 按设计
    兜底成 `chitchat.talk`，于是**6 条组合意图用例被记成 `stable_fail`**——重跑全绿。
    这与本套件首跑自查的 3.1/3.4 是同一形态：**失败被记成了别的东西**。
    首跑时只在范例预热那一处防住了（发现清单 §3-2），逐轮的规划调用没防。

    判据用 `raw_llm` 而不是「计划长得像兜底」：只要模型回过话，`raw_llm` 就非空
    （JSON 路径存原文、toolcall 路径存序列化后的 arguments）。**`raw_llm` 为空且仍出了
    计划，只可能来自 `_fallback`**——那说明两次调用都没拿到任何输出，是通道问题不是判断问题。
    模型「回了但答得不好」照常算产品失败，一分不放水。
    """
    plan = snapshot.plan
    if plan.steps and not plan.raw_llm:
        raise RuntimeError(
            f"planner_unreached: {case.id} 两次规划调用都没拿到模型输出"
            f"（plan_mode={plan.plan_mode!r}，计划来自 fallback）——"
            f"这是网关/额度问题，不是落域缺陷，修好重跑")


def run_l2_case(case, agents, builder, confirm_intents) -> list["TurnOutcome"]:
    """**同步**：L2 的三个部件（seed、Edge servicer、Engine）各自驱动自己的事件循环。

    这里原本是 async，于是 `asyncio.run(run_l2_case(...))` 之后再在里面
    `asyncio.run(session.save(...))` —— 在运行中的 loop 里再 run 会直接抛，整层被
    吞成基础设施错误、`cases=0`。同一个坑在 `EngineHarness` 那儿已经踩过一次
    （见 `run_async` 的注释），这次是在更外面一层。"""
    first = case.turns[0]
    sink = TraceSink()
    clients = runtime.SafeClients(
        agents,
        history=list(first.context.get("history") or []),
        memories=list(first.context.get("memories") or []),
        confirm_intents=confirm_intents)
    harness = runtime.build_engine_harness(builder, agents, clients, sink)
    session_id = f"adv-{case.id}"
    focus = first.context.get("focus") or {}
    if focus:
        harness.seed_focus(session_id, focus)
    pending = first.context.get("pending_confirm") or {}
    if pending:
        harness.seed_pending_confirm(session_id, intent=str(pending.get("intent") or ""),
                                     step_id=str(pending.get("step_id") or "s1"))
    entry = runtime.FullEntrySession(harness, session_id=session_id)
    outcomes: list[TurnOutcome] = []
    with probe_builder(builder, sink):
        for turn in case.turns:
            before = (len(sink.validations), len(sink.hints), len(sink.plans))
            edge, engine = entry.turn(
                turn.utterance,
                is_confirmation=bool(turn.context.get("is_confirmation")),
                meta={k: str(v) for k, v in (turn.context.get("meta") or {}).items()})
            plans = [trace.plan for trace in sink.plans[before[2]:]]
            snapshot = DecisionSnapshot(
                ingress=edge.ingress,
                addressed=True,
                decision=(engine.decision if engine else "execute"),
                clarify=bool(engine and engine.decision == "clarify"),
                plan=plans[0] if plans else PlanSnapshot.empty(),
                replans=tuple(plans[1:]),
                # **Edge 与 Engine 的副作用必须合并。** 只取 Engine 那一半时，
                # 「Edge 提前把车控执行了」在最危险的一类回归上保持绿灯：
                # `run_full_entry_turn` 明明同时返回了 Edge 观测，却被丢掉。
                side_effects=(_engine_side_effects(engine)
                              + runtime.edge_side_effect_rows(edge)),
                engine_observed=engine is not None,
                agent_calls=tuple(row["intent"] for row in
                                  (engine.agent_calls if engine else ())),
                pending_confirm_after=(engine.pending_confirm_after
                                       if engine else None))
            outcomes.append(_turn_outcome(turn, snapshot,
                                          judge_turn(turn.expected, snapshot),
                                          sink, before[:2]))
    return outcomes


def _engine_side_effects(engine) -> tuple[dict[str, Any], ...]:
    return tuple({"source": "engine", "intent": row.get("intent"),
                  "confirmed": row.get("confirmed"), "action": row.get("action")}
                 for row in (engine.side_effects if engine else ()))


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


def read_l3_report(artifact_root: Path, *, since: float | None = None
                   ) -> tuple[dict[str, str], list[str]]:
    """从 runner 产出的结构化 journeys report 读逐条状态；只看退出码等于没读。

    `since` 是本次调用的开始时间：**旧报告不许冒充本次证据**。固定目录 + 递归读全部
    `journeys_report.json` 时，只要目录里曾经成功过一次，后面失败的运行就能读到那份
    旧的 `pass`——反向构造里 `exit=2` 配一份陈旧 pass 得到的 `infrastructure_errors`
    是空的。
    """
    statuses: dict[str, str] = {}
    stale: list[str] = []
    for path in sorted(Path(artifact_root).glob("**/journeys_report.json")):
        try:
            if since is not None and path.stat().st_mtime < since:
                stale.append(str(path))
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in data.get("journeys") or []:
            if isinstance(row, dict) and row.get("id"):
                statuses[str(row["id"])] = str(row.get("status") or "")
    return statuses, stale


# ── 结果装配 ───────────────────────────────────────────────────────────────


def _gold_intents(turn) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(可归因的 gold intent, gold domain)。

    多成员 `any_of` 只表示「这一组任一可接受」，不能替其中某个成员归因，所以 intent
    维度只取单成员必要组——与 coverage 的口径逐字一致。domain 维度可以放宽一档：
    整组成员同域时那个域就是明确的期望域。
    """
    plan = turn.expected.plan
    intents = tuple(sorted({group.any_of[0] for group in plan.required_groups
                            if len(group.any_of) == 1}))
    domains = {intent.split(".", 1)[0] for intent in intents}
    for group in plan.required_groups:
        members = {intent.split(".", 1)[0] for intent in group.any_of}
        if len(members) == 1:
            domains |= members
    return intents, tuple(sorted(domains))


def _dimensions(case, turn, layer: str, snapshot: DecisionSnapshot,
                provider_model: str) -> dict[str, tuple[str, ...]]:
    """gold 维度与 actual 维度**分开**记。

    原来只按实际 plan 分桶：`ex.homophone.charging` 期望 `charging.find`、实际跑去
    `nearby.search`，这条失败于是记在 nearby 头上，charging 那一格反而 13/13 满分。
    「完全漏接的目标域」正是最该被最弱 cell 抓住的东西。
    """
    actual = tuple(snapshot.plan.intents)
    gold_intents, gold_domains = _gold_intents(turn)
    declared = tuple(str(d) for d in (case.tags.get("domains") or []))
    return {
        "expected_intent": gold_intents,
        "expected_domain": gold_domains or declared,
        "actual_intent": actual,
        "actual_domain": tuple(sorted({i.split(".", 1)[0] for i in actual})),
        "boundary": tuple(filter(None, (str(case.tags.get("boundary") or ""),
                                        str(case.tags.get("boundary_ledger") or "")))),
        "attack": tuple(str(a) for a in (case.tags.get("attacks") or [])),
        "risk": (case.risk,),
        "ingress": (snapshot.ingress,),
        "cohort": (case.cohort,),
        "layer": (layer,),
        "provider": (provider_model,),
        "status": (case.status,),
        "provenance": (str(case.provenance.get("kind") or "authored"),),
    }


def _metrics_for(turn, judgement, snapshot: DecisionSnapshot) -> dict[str, float]:
    """指标只在**真的被断言过**的维度上记。

    `exact_plan_set` 原来直接拿整轮 `judgement.passed`：于是 L0（根本没有 plan 断言）
    也在往这个指标里记 1，而 plan 三项全过、只有检索断言失败的用例被记成 0——
    这个数既不是 plan 精确率，也不是通过率。
    """
    metrics: dict[str, float] = {}
    exact = judgement.subset_passed("plan.", "replan[")
    if exact is not None:
        metrics["exact_plan_set"] = 1.0 if exact else 0.0
    for key in ("required_group_recall", "forbidden_route_count", "overroute_count",
                "dependency_pass"):
        if key in judgement.metrics:
            metrics[key] = judgement.metrics[key]
    # ingress 两条断言取 AND。原来后一次赋值直接盖掉前一次，于是
    # 「allowed 失败 + forbidden 通过」最终记成 1——ingress 准确率整体偏高。
    ingress = judgement.subset_passed("ingress_allowed", "ingress_forbidden")
    if ingress is not None:
        metrics["ingress_pass"] = 1.0 if ingress else 0.0
    clarify = judgement.subset_passed("clarify")
    if clarify is not None:
        side = ("clarify_required_pass" if turn.expected.clarify == "required"
                else "clarify_forbidden_pass")
        metrics[side] = 1.0 if clarify else 0.0
    if "forbidden_agent_call_count" in judgement.metrics:
        metrics["forbidden_agent_call_count"] = \
            judgement.metrics["forbidden_agent_call_count"]
    return metrics


def _dangerous(case, turn, judgement, snapshot: DecisionSnapshot) -> bool:
    if snapshot.side_effects and turn.expected.no_side_effect_before_confirm:
        return True
    if judgement.metrics.get("forbidden_agent_call_count", 0.0):
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
        "engine_observed": snapshot.engine_observed,
        "agent_calls": list(snapshot.agent_calls),
        "pending_confirm_after": snapshot.pending_confirm_after,
    }


def repro_command(case, layer: str, args: argparse.Namespace) -> str:
    """从**实际运行的那一层与那个 provider** 生成，不是从声明的层列表拼。

    原来这里把 `layers: [l1, l2]` 拼成 `--layer l1/l2`（argparse 不接受），还漏掉
    `--live --provider --model`——L1/L2 的失败一条都复现不了。relation variant 还必须
    自动带上 base，否则 relation 断言在复现里根本不会被裁。
    """
    parts = ["python", "test/eval_intent_adversarial.py", "--case", case.id]
    if case.relation and case.relation.base_case:
        parts += ["--case", case.relation.base_case]
    parts += ["--suite", args.suite, "--layer", layer]
    if layer in LIVE_LAYERS:
        parts += ["--live", "--provider", args.provider or "<provider>",
                  "--model", args.model or "<model>"]
    parts += ["--repeat", "3", "--diagnose"]
    return " ".join(parts)


def _expected_dict(case, turn, index: int, layer: str,
                   args: argparse.Namespace) -> dict[str, Any]:
    plan = turn.expected.plan
    engine = turn.expected.engine
    return {
        "turn_index": index,
        "utterance": turn.utterance,
        "context": turn.context,
        "ingress_allowed": list(turn.expected.ingress_allowed),
        "ingress_forbidden": list(turn.expected.ingress_forbidden),
        "decision_allowed": list(turn.expected.decision_allowed),
        "clarify": turn.expected.clarify,
        "required_intent_groups": [list(g.any_of) for g in plan.required_groups],
        "forbidden_intents": list(plan.forbidden_intents),
        "allow_extra_intents": plan.allow_extra_intents,
        "engine": ({} if not engine.declared else {
            "required_agent_calls": list(engine.required_agent_calls),
            "forbidden_agent_calls": list(engine.forbidden_agent_calls),
            "pending_confirm_after": engine.pending_confirm_after,
            "max_agent_calls_per_intent": engine.max_agent_calls_per_intent}),
        "no_side_effect_before_confirm": turn.expected.no_side_effect_before_confirm,
        "relation": (None if not case.relation else
                     {"base_case": case.relation.base_case, "type": case.relation.type,
                      "expectation": case.relation.expectation}),
        "repro": repro_command(case, layer, args),
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


def repo_relative(path) -> str | None:
    """仓库相对路径；**跨盘/不在仓库内返回 None，不抛异常**。

    `os.path.relpath("C:/tmp/x.json", "D:/repo")` 在 Windows 直接抛 `ValueError`，
    而它被放在整跑结束后的 meta 组装里——L0 跑完 70 条才 traceback，退出码还是 1，
    看起来像语义失败。
    """
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None


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
    print(f"suite={args.suite} layer={args.layer} selected={len(cases)} "
          f"distinct_inputs={contract.distinct_input_units(cases)}")
    duplicates = contract.duplicate_input_groups(cases)
    if duplicates:
        print(f"duplicate inputs: {len(duplicates)} 组"
              f"（规模只计一个单位）：{json.dumps(list(duplicates.values())[:5], ensure_ascii=False)}")
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


def knowledge_utterances() -> set[str]:
    """注入给模型的知识里出现过的字面话术（Skill guide / Exemplar / 边界台账）。"""
    paths = sorted((ROOT / "skills").glob("**/*.yaml"))
    return contract.load_knowledge_utterances(paths)


def _gather_contract_errors(cases, suite, args) -> tuple[list[str], list[str]]:
    """硬错误（永远阻断）与缺口（普通 --list 只展示，strict 才升级）分开返回。"""
    active = eval_live.known_intents()
    hard = contract.validate_cases(cases, active)
    # 原句泄漏按**输入事实**判：`unseen_transfer` 的话术不得字面出现在被注入的知识里。
    # family_id 只能防住「作者记得它们同源」的那一半。
    hard += contract.validate_cohort_isolation(cases, knowledge_utterances())
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
    # **空选集不是绿灯。** `--case <打错的 id>` 或 `--layer` 与 case 声明的层不相交时，
    # 原来会跑完 0 条然后 exit=0——自动化读到的是「全过」。
    expected_units = _expected_units(selected, args)
    if not expected_units:
        print(f"[intent-adversarial] 选集为空（suite={args.suite} layer={args.layer}"
              f" cases={len(selected)}）：没有任何证据单元可跑，不是通过",
              file=sys.stderr)
        return 2

    lock = None
    infrastructure_errors: list[str] = []
    results: list[AdversarialResult] = []
    provider_model = "deterministic"
    warmed = 0
    l3_selected: list[str] = []
    l3_statuses: dict[str, str] = {}
    l3_meta: dict[str, Any] = {}
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
        l3_selected, l3_statuses, l3_infra, l3_meta = _l3_evidence(
            selected, args, provider_model)
        infrastructure_errors.extend(l3_infra)
        results.extend(_l3_results(selected, args, l3_statuses, provider_model))
    except RuntimeError as exc:
        print(f"[intent-adversarial] infrastructure error: {exc}", file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.restore()

    filters = selection_filters(args)
    meta = {
        "suite": args.suite, "layer": args.layer,
        "retrieval_state": args.retrieval_state, "warmed_exemplars": warmed,
        "provider_locked": bool(lock and lock.locked),
        "provider_drift": bool(lock and lock.drifts),
        "provider_lock": (lock.summary() if lock else {}),
        "provider_model": provider_model,
        "code_sha": eval_common.git_short_sha(),
        "worktree_clean": _worktree_clean(
            {value for value in (repo_relative(args.out_json),
                                 repo_relative(args.out_md)) if value}),
        "assets": asset_fingerprint(ROOT),
        "infrastructure_errors": infrastructure_errors,
        "selected_statuses": sorted({case.status for case in selected}),
        "coverage_gaps": gaps,
        "selection_filters": filters,
        "repeat_override": int(args.repeat or 0),
        "corpus_cases": len(all_cases),
        "selected_cases": len(selected),
        "distinct_input_units": contract.distinct_input_units(selected),
        "duplicate_input_groups": contract.duplicate_input_groups(selected),
        "l3_selected": l3_selected,
        "l3_complete": bool(l3_selected) and all(
            l3_statuses.get(j) == "pass" for j in l3_selected),
        "l3_evidence_fresh": bool(l3_meta.get("fresh")) if l3_meta else False,
        "l3_invocation": l3_meta,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["assets_complete"] = bool(meta["assets"].get("complete"))
    produced = {r.result_id for r in results}
    # 「当前选集跑齐了」证明不了「选集等于完整声明集」。两件事分开记：
    # `case_set_complete` 管前者，`declared_set_complete` 管后者。
    declared_units = _expected_units(
        [case for case in all_cases if case.status in set(suite.statuses)], args)
    meta["case_set_complete"] = expected_units <= produced
    meta["declared_set_complete"] = bool(declared_units) and declared_units <= produced
    meta["missing_declared_units"] = sorted(declared_units - produced)[:50]
    meta["repeat_policy_complete"] = _repeat_policy_complete(results, suite)
    baseline = eval_common.load_baseline(Path(args.baseline))
    report = build_adversarial_report(results, meta)
    if baseline:
        diff = eval_common.diff_against_baseline(report, baseline)
        report["meta"]["baseline_regressions"] = [cid for cid, _, _ in diff.regressions]
        # 旧 baseline 里有、这次不见了的证据单元：删掉难例同样能让门禁变绿。
        report["meta"]["removed_cases"] = sorted(
            {str(row.get("id") or "") for row in (baseline.get("cases") or [])}
            - produced - {""})[:50]
    else:
        report["meta"]["baseline_regressions"] = []
        report["meta"]["removed_cases"] = []
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
    if args.diagnose:
        _print_diagnosis(report)
    if infrastructure_errors:
        for row in infrastructure_errors[:20]:
            print(f"[infra] {row}", file=sys.stderr)
        return 2
    if gaps and (args.strict or args.write_baseline):
        for row in gaps[:50]:
            print(f"[coverage] {row}", file=sys.stderr)
        return 2
    failed = report["overall"]["passed"] != report["overall"]["total"]
    if failed or report["meta"]["baseline_regressions"] or meta["provider_drift"]:
        return 1
    return 0


def _expected_units(cases, args) -> set[str]:
    """一次运行**应当**产出的证据单元全集（含多轮逐轮单元与有链接的 L3 单元）。"""
    links = load_journey_links()
    units: set[str] = set()
    for case in cases:
        turns = len(case.turns)
        for layer in layers_for(case, args.layer):
            if layer == "l3":
                if links.get(case.id):
                    units.add(f"{case.id}@l3")
                continue
            for index in range(turns):
                units.add(_unit_id(case, layer, index, turns))
    return units


def _repeat_policy_complete(results, suite) -> bool:
    """重复次数不达标一律不合资格。

    `--repeat 1` 能把「高风险固定三次」降成一次；只检查「跑齐了选集」看不见这件事。
    """
    for row in results:
        if row.layer not in LIVE_LAYERS or row.layer == "l3":
            continue
        need = suite.high_risk_repeats if row.risk in {"high", "critical"} else 1
        if not row.passed:
            need = max(need, suite.failure_repeats)
        if len(row.repetitions) < need:
            return False
    return True


def _print_diagnosis(report: dict) -> None:
    """`--diagnose` 的消费方：单案例诊断包（规格 §14.3）。

    这个开关原来**没有任何消费方**——报告里生成的复现命令带着它，跑起来却什么都不多。
    """
    for unit, row in sorted((report.get("results") or {}).items()):
        print(f"\n── {unit} [{row['repeat_status']}] {row['title']}")
        print(f"   输入: {row['expected'].get('utterance')!r} "
              f"ctx={json.dumps(row['expected'].get('context') or {}, ensure_ascii=False)}")
        print(f"   实际: ingress={row['actual'].get('ingress')} "
              f"decision={row['actual'].get('decision')} "
              f"steps={[s['intent'] for s in row['actual'].get('steps') or []]}")
        if row["actual"].get("engine_observed"):
            print(f"   Engine: agent_calls={row['actual'].get('agent_calls')} "
                  f"pending_after={row['actual'].get('pending_confirm_after')} "
                  f"side_effects={row['actual'].get('side_effects')}")
        print(f"   检索: skills={row['actual'].get('skills')} "
              f"exemplars={row['actual'].get('exemplars')}")
        for assertion in row.get("assertions") or []:
            mark = "OK " if assertion["passed"] else "FAIL"
            print(f"   [{mark}] {assertion['name']}: "
                  f"expected={assertion['expected']} actual={assertion['actual']}")
        print(f"   重复: {[r['passed'] for r in row.get('repetitions') or []]}")
        print(f"   首偏离: {row.get('divergence') or 'NONE'} "
              f"候选={list(row.get('divergence_candidates') or ())} "
              f"证据={json.dumps(row.get('divergence_evidence') or {}, ensure_ascii=False)}")
        if row.get("ablations"):
            print(f"   消融: {json.dumps(row['ablations'], ensure_ascii=False)}")
        print(f"   复现: {row['expected'].get('repro')}")


def _print_summary(report: dict) -> None:
    metrics = report["metrics"]
    meta = report.get("meta") or {}
    print(f"units={report['overall']['total']} passed={report['overall']['passed']} "
          f"cases={meta.get('selected_cases', 0)} "
          f"distinct_inputs={meta.get('distinct_input_units', 0)}")
    for name in ("exact_plan_set_rate", "required_group_recall",
                 "forbidden_route_rate", "planner_capability_hallucination_rate",
                 "post_validation_escape_rate", "instability_rate",
                 "repeat_coverage"):
        row = metrics.get(name) or {}
        value = row.get("value")
        print(f"  {name}: {'null' if value is None else f'{value * 100:.1f}%'}"
              f" ({row.get('numerator', 0):g}/{row.get('denominator', 0):g})")
    for row in (report.get("weakest") or [])[:5]:
        print(f"  weakest {row['dimension']}={row['cell']} "
              f"{row['pass_rate'] * 100:.1f}% (n={row['total']})")


def l3_invocation_id(code_sha: str, now: datetime | None = None) -> str:
    """时间戳 + pid + 随机尾巴。

    只用时间戳不够：同一微秒内两次调用会拿到同一个 id，于是两次运行又共用一个目录——
    这正是本条要修的那个问题的另一种形态。
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{os.getpid()}-{uuid.uuid4().hex[:6]}-{code_sha or 'nosha'}"


def _l3_evidence(selected, args, provider_model
                 ) -> tuple[list[str], dict[str, str], list[str], dict[str, Any]]:
    if args.layer not in {"l3", "all"} or not args.live:
        return [], {}, [], {}
    links = load_journey_links()
    ids = sorted({j for case in selected
                  if "l3" in layers_for(case, args.layer) for j in links.get(case.id, [])})
    if not ids:
        return [], {}, [], {}
    # **每次唯一 run 目录。** 固定目录会让上一次成功的报告留在原地，下一次失败运行照样
    # 读得到它的 `pass`——旧证据于是能写进正式 baseline。
    code_sha = eval_common.git_short_sha()
    invocation = l3_invocation_id(code_sha)
    started = datetime.now(timezone.utc)
    artifact_root = (ROOT / "docs" / "reviews" / "eval"
                     / "_ci-run-intent-l3-artifacts" / invocation)
    code = run_l3(ids, provider=args.provider, model=args.model,
                  artifact_root=artifact_root)
    statuses, stale = read_l3_report(artifact_root, since=started.timestamp())
    infra: list[str] = []
    meta = {
        "invocation_id": invocation, "started_at": started.isoformat(),
        "code_sha": code_sha, "provider_model": provider_model,
        "provider": args.provider, "model": args.model,
        "journey_ids": list(ids), "exit_code": code,
        "artifact_root": str(artifact_root.relative_to(ROOT).as_posix()),
        "stale_reports_ignored": stale,
    }
    if code != 0:
        # **非零退出一律基础设施失败。** 之前只在「读不到任何 statuses」时才记，于是
        # 只要目录里读得到东西，runner 崩了也不算错。
        infra.append(
            f"l3_runner_failed: scripts/run_e2e.py exit={code}"
            f"（选集 {','.join(ids)}，invocation {invocation}）"
            "——运行器故障不折算成 journey 失败")
    if not statuses:
        infra.append(
            f"l3_report_missing: 本次运行未产出结构化 journeys_report"
            f"（invocation {invocation}）")
    missing = [j for j in ids if j not in statuses]
    if statuses and missing:
        infra.append(f"l3_report_incomplete: 报告里缺 {','.join(missing)}")
    if stale:
        infra.append(f"l3_stale_reports: 忽略了 {len(stale)} 份早于本次开始时间的报告")
    meta["fresh"] = not infra
    return ids, statuses, infra, meta


def repeat_plan(selected, args, suite) -> dict[str, int]:
    """每条 case 的重复次数。relation 成对时两边取 max——否则配不成对。

    base 是 medium（1 次）而 variant 是 high（3 次）时，逐次成对裁 relation 就少了
    两个 base 样本；把 base 也提到 3 次是唯一不需要「拿第 1 次凑第 3 次」的做法。
    """
    base_reps = {}
    for case in selected:
        base_reps[case.id] = args.repeat or (
            suite.high_risk_repeats if case.risk in {"high", "critical"} else 1)
    by_id = {case.id: case for case in selected}
    for case in selected:
        if not case.relation:
            continue
        other = by_id.get(case.relation.base_case)
        if other is None:
            continue
        target = max(base_reps[case.id], base_reps[other.id])
        base_reps[case.id] = base_reps[other.id] = target
    return base_reps


def _relation_partners(selected) -> dict[str, str]:
    """case_id → 与它成对的另一条（双向）。用于失败扩展时同步扩另一边。"""
    partners: dict[str, str] = {}
    ids = {case.id for case in selected}
    for case in selected:
        if case.relation and case.relation.base_case in ids:
            partners[case.id] = case.relation.base_case
            partners[case.relation.base_case] = case.id
    return partners


def _execute(selected, args, suite, agents, builder, confirm_intents,
             provider_model, lock) -> tuple[list[AdversarialResult], list[str]]:
    infra: list[str] = []
    reps = repeat_plan(selected, args, suite)
    partners = _relation_partners(selected)
    units: dict[str, UnitRuns] = {}
    checked = 0

    def run_once(case, layer) -> list[TurnOutcome]:
        if layer == "l0":
            return run_l0_case(case)
        if builder is None:
            raise RuntimeError(f"layer {layer} 需要 live builder")
        if layer == "l1":
            return asyncio.run(run_l1_case(case, agents, builder))
        return run_l2_case(case, agents, builder, confirm_intents)

    for case in selected:
        for layer in layers_for(case, args.layer):
            if layer == "l3":
                continue                       # L3 证据由 _l3_evidence 回填
            key = f"{case.id}@{layer}"
            unit = UnitRuns(case=case, layer=layer)
            try:
                for _ in range(reps[case.id]):
                    unit.runs.append(run_once(case, layer))
            except RuntimeError as exc:
                infra.append(f"{key}: {exc}")
                continue
            units[key] = unit
            checked += 1
            if lock is not None and checked % 20 == 0:
                lock.check(key)

    # 失败扩展：首跑失败且只跑了一次 → 补到 failure_repeats。成对的两条同步扩，
    # 否则第 2/3 次没有 base 可配。
    if not args.repeat:
        _expand_failures(units, partners, suite, args, run_once, infra)
    if lock is not None:
        lock.check("final")

    relation_judgements, relation_gaps = _relation_judgements(units, args)
    # relation 配不成对不是「这条恰好没有 relation 断言」——它是**少裁了一条 gold**。
    # 静默跳过会让 variant 拿着不完整的判定通过。
    infra.extend(relation_gaps)
    results: list[AdversarialResult] = []
    for key in sorted(units):
        results.extend(_assemble_unit(
            units[key], args, agents, builder, confirm_intents, provider_model,
            relation_judgements.get(key, {})))
    return results, infra


def _expand_failures(units, partners, suite, args, run_once, infra) -> None:
    wanted = {key for key, unit in units.items()
              if unit.layer != "l0" and len(unit.runs) == 1
              and any(not outcome.judgement.passed for outcome in unit.runs[0])}
    for key in sorted(wanted):
        partner = partners.get(units[key].case.id)
        keys = [key] + ([f"{partner}@{units[key].layer}"] if partner else [])
        for target in keys:
            unit = units.get(target)
            if unit is None or len(unit.runs) >= suite.failure_repeats:
                continue
            try:
                while len(unit.runs) < suite.failure_repeats:
                    unit.runs.append(run_once(unit.case, unit.layer))
            except RuntimeError as exc:
                infra.append(f"{target}: {exc}")


def _relation_judgements(units, args) -> tuple[dict[str, dict[int, Any]], list[str]]:
    """**逐次重复**成对裁 relation；配不成对时报基础设施错误而不是静默跳过。

    原来 relation 在重复分类之后才追加，只改 `passed` 不改 `repeat_status`——
    第二趟产物里出现过 3 条 `passed=false` 但 `repeat_status=pass` 的自相矛盾行。
    这里把 relation 判定折进每一次 repetition，分类因此看得见它。
    """
    out: dict[str, dict[int, Any]] = {}
    gaps: list[str] = []
    for key, unit in units.items():
        case = unit.case
        if not case.relation:
            continue
        base_key = f"{case.relation.base_case}@{unit.layer}"
        base = units.get(base_key)
        if base is None:
            gaps.append(f"relation_base_missing: {key} 的对照 {base_key} 没有结果，"
                        "relation gold 本轮未被裁——不算通过")
            continue
        pairs = min(len(unit.runs), len(base.runs))
        if pairs < len(unit.runs):
            gaps.append(f"relation_pairs_incomplete: {key} 跑了 {len(unit.runs)} 次，"
                        f"对照只有 {len(base.runs)} 次，只成对裁了 {pairs} 次")
        for index in range(pairs):
            variant_turns, base_turns = unit.runs[index], base.runs[index]
            if not variant_turns or not base_turns:
                continue
            # 契约保证 relation 双方各只有一轮（schema v1）。
            out.setdefault(key, {})[index] = judge_relation(
                case.relation, base_turns[0].snapshot, variant_turns[0].snapshot)
    return out, gaps


def _unit_id(case, layer: str, turn_index: int, turns: int) -> str:
    """单轮沿用 `case_id@layer`；多轮逐轮独立成 `case_id#turn@layer`。"""
    return (f"{case.id}@{layer}" if turns <= 1
            else f"{case.id}#{turn_index + 1}@{layer}")


def _assemble_unit(unit: UnitRuns, args, agents, builder, confirm_intents,
                   provider_model, relations: dict[int, Any]
                   ) -> list[AdversarialResult]:
    case, layer = unit.case, unit.layer
    turns = unit.turn_count()
    results: list[AdversarialResult] = []
    for turn_index in range(turns):
        turn = case.turns[turn_index]
        outcomes = [run[turn_index] for run in unit.runs]
        repeats: list[runtime.RepeatOutcome] = []
        for index, outcome in enumerate(outcomes):
            relation = relations.get(index) if turn_index == 0 else None
            passed = outcome.judgement.passed and (relation is None
                                                   or relation.passed)
            repeats.append(runtime.RepeatOutcome(
                passed=passed,
                signature=repr(semantic_signature(outcome.snapshot)),
                dangerous=_dangerous(case, turn, outcome.judgement,
                                     outcome.snapshot)))
        classification = runtime.classify_repeats(repeats, case.risk,
                                                  deterministic=layer == "l0")
        # 判定为失败时，留**失败那一轮**的证据。首轮通过、第二三轮翻车的案例（高风险
        # 固定跑 3 次，这种最常见）如果只存首轮，报告里会出现「stable_fail 但断言全绿」
        # ——诊断当场归零，反而要人回头重跑一次才知道错在哪。
        evidence = 0
        if classification.status != "pass":
            evidence = next((i for i, o in enumerate(repeats) if not o.passed), 0)
        outcome = outcomes[evidence]
        judgement = outcome.judgement
        relation = relations.get(evidence) if turn_index == 0 else None
        metrics = _metrics_for(turn, judgement, outcome.snapshot)
        assertions = _assertion_rows(judgement)
        if relation is not None:
            metrics["relation_pass"] = relation.metrics["relation_pass"]
            if case.relation and case.relation.type == "context_override":
                metrics["context_override_pass"] = relation.metrics["relation_pass"]
            assertions = assertions + _assertion_rows(relation)
        ablations = _run_ablations(case, layer, args, classification, agents,
                                   builder, confirm_intents)
        evidence_row = DivergenceEvidence(
            full_entry_pass=classification.status == "pass",
            engine_direct_pass=_arm_result(ablations, "cloud-direct"),
            planner_post_hint_pass=_arm_result(ablations, "planner-only"),
            empty_history_pass=_arm_result(ablations, "empty-history"),
            retrieval_ablation_pass=_any_arm_result(ablations, "no-skills",
                                                    "no-exemplars"),
            pre_hint_pass=outcome.pre_hint_pass,
            raw_planner_pass=outcome.raw_planner_pass)
        if layer == "l0":
            divergence = ("" if classification.status == "pass"
                          else deterministic_divergence(
                              [row["name"] for row in assertions
                               if not row["passed"]]))
            candidates: tuple[str, ...] = ()
            evidence_dict: dict[str, Any] = {"layer": "l0-deterministic"}
        else:
            divergence = ("" if classification.status == "pass"
                          else first_divergence(evidence_row))
            candidates = divergence_candidates(evidence_row)
            evidence_dict = trace_evidence_dict(evidence_row)
        results.append(AdversarialResult(
            result_id=_unit_id(case, layer, turn_index, turns),
            case_id=case.id, layer=layer,
            title=case.title if turns <= 1 else f"{case.title}（第 {turn_index + 1} 轮）",
            passed=classification.status == "pass",
            repeat_status=classification.status, cohort=case.cohort, risk=case.risk,
            status=case.status, provenance_kind=str(case.provenance.get("kind") or ""),
            provider_model=provider_model if layer != "l0" else "deterministic",
            dimensions=_dimensions(case, turn, layer, outcome.snapshot,
                                   provider_model if layer != "l0" else "deterministic"),
            metrics=metrics,
            expected=_expected_dict(case, turn, turn_index, layer, args),
            actual=_snapshot_dict(outcome.snapshot),
            # 准入清单按本案例的 catalog 条件算：A8 把某个能力从 catalog 拿掉后，
            # 计划里还出现它就是能力幻觉——用全量 inventory 当分母会看不见这件事。
            admitted_intents=_admitted_intents(turn, agents),
            actual_intents=tuple(outcome.snapshot.plan.intents),
            raw_intents=outcome.raw_intents, raw_observed=outcome.raw_observed,
            assertions=assertions,
            repetitions=tuple({"passed": o.passed, "signature": o.signature[:400],
                               "dangerous": o.dangerous}
                              for o in classification.outcomes),
            divergence=divergence, divergence_candidates=candidates,
            divergence_evidence=evidence_dict,
            ablations=tuple(ablations)))
    return results


def _admitted_intents(turn, agents) -> tuple[str, ...]:
    catalog = runtime.filter_unavailable_capabilities(
        agents, set(turn.context.get("unavailable_intents") or []))
    return tuple(sorted(
        str(cap.intent) for agent in catalog
        for cap in (getattr(agent.manifest, "capabilities", None) or [])
        if getattr(cap, "intent", "")))


def _l3_results(selected, args, l3_statuses, provider_model) -> list[AdversarialResult]:
    """把 journey 逐条状态回填成 `case_id@l3` 证据单元。

    只看子进程退出码等于没读结构化结果——一个 journey 红、另一个绿会被压成同一个
    非零码。链接到同一条 case 的每个 journey 都必须通过，才算这条 case 的 L3 证据通过。
    """
    if args.layer not in {"l3", "all"} or not l3_statuses:
        return []
    links = load_journey_links()
    rows: list[AdversarialResult] = []
    for case in selected:
        if "l3" not in layers_for(case, args.layer):
            continue
        journeys = links.get(case.id) or []
        if not journeys:
            continue
        statuses = {j: l3_statuses.get(j, "missing") for j in journeys}
        passed = all(value == "pass" for value in statuses.values())
        rows.append(AdversarialResult(
            result_id=f"{case.id}@l3", case_id=case.id, layer="l3",
            title=case.title, passed=passed,
            repeat_status="pass" if passed else "stable_fail",
            cohort=case.cohort, risk=case.risk, status=case.status,
            provenance_kind=str(case.provenance.get("kind") or ""),
            provider_model=provider_model,
            dimensions={
                "expected_intent": (),
                "expected_domain": tuple(str(d) for d in
                                         (case.tags.get("domains") or [])),
                "actual_intent": (), "actual_domain": (),
                "boundary": (), "attack": tuple(str(a) for a in
                                                (case.tags.get("attacks") or [])),
                "risk": (case.risk,), "ingress": ("cloud",),
                "cohort": (case.cohort,), "layer": ("l3",),
                "provider": (provider_model,), "status": (case.status,),
                "provenance": (str(case.provenance.get("kind") or ""),)},
            # L3 是旅程级红绿，没有 plan 断言——不进 `exact_plan_set` 的分母。
            metrics={},
            expected={"utterance": case.turns[0].utterance,
                      "journeys": journeys,
                      "repro": "python scripts/run_e2e.py --id e2e_journeys "
                               f"--provider {args.provider} --model {args.model} "
                               f"（E2E_JOURNEY_IDS={','.join(journeys)}）"},
            actual={"journey_statuses": statuses},
            admitted_intents=(), actual_intents=(),
            assertions=tuple({"name": f"journey:{j}", "passed": s == "pass",
                              "expected": "pass", "actual": s}
                             for j, s in statuses.items()),
            # journey 红灯没有分层对照物，声称首偏离点是 Planner 只是在编。
            repetitions=(), divergence="" if passed else "UNCLASSIFIED"))
    return rows


def _arm_result(ablations: list[dict], arm: str) -> bool | None:
    """`None` = 这条 arm 没跑（不是「跑了没翻正」）。两者不能混，见 first_divergence。"""
    for row in ablations:
        if row["arm"] == arm:
            return None if row["status"] == "error" else row["status"] == "pass"
    return None


def _any_arm_result(ablations: list[dict], *arms: str) -> bool | None:
    values = [_arm_result(ablations, arm) for arm in arms]
    if any(value is True for value in values):
        return True
    return False if all(value is False for value in values) else None


def _run_ablations(case, layer, args, classification, agents, builder,
                   confirm_intents=None) -> list[dict]:
    """arm 按 **layer** 取。

    原来无论失败来自 L1 还是 L2，消融都只跑 `run_l1_case()`，`cloud-direct` 还被
    显式 `continue` 跳过——`EDGE_DIVERGENCE` 与 `STATE_RESTORE_DIVERGENCE` 两个边界
    因此结构上永远不可达，而它们正是 L2 存在的理由。
    """
    if args.ablations != "on-failure" or layer == "l0" or builder is None:
        return []
    rows: list[dict] = []
    for arm in runtime.requested_ablations(classification.status, layer):
        try:
            status = _run_single_ablation(case, layer, arm, args, agents,
                                          confirm_intents)
        except RuntimeError as exc:
            rows.append({"arm": arm, "status": "error", "detail": str(exc),
                         "causal": "invalid"})
            continue
        rows.append({"arm": arm, "status": status,
                     "causal": runtime.causal_effect(classification.status, status,
                                                     True, True)})
    return rows


def _run_single_ablation(case, layer, arm, args, agents, confirm_intents) -> str:
    """一个 arm 跑满 3 次再分类：一次翻转是噪声也解释得通（§11.4 suspect vs causal）。

    `cloud-direct` / `planner-only` 是**对照层**而不是变量消融：前者绕开 Edge 直连
    Engine，后者只跑 Planner 不恢复会话状态；两者都保持完整检索与 Hint 装配。
    """
    ablated = replace(case, turns=tuple(
        replace(turn, context=runtime.ablation_context(arm, turn.context))
        for turn in case.turns))
    outcomes = []
    with runtime.temporary_env(runtime.ablation_env(arm)):
        for _ in range(3):
            arm_builder = eval_live.make_builder("intent-adversarial-ablation",
                                                 args.temperature, timeout=args.timeout)
            if arm == "no-hints":
                runtime.disable_route_hints(arm_builder)
            if arm == "cloud-direct":
                turn_outcomes = _run_engine_direct(ablated, agents, arm_builder,
                                                   confirm_intents or set())
            else:
                turn_outcomes = asyncio.run(run_l1_case(ablated, agents, arm_builder))
            passed = all(row.judgement.passed for row in turn_outcomes)
            signature = repr(tuple(semantic_signature(row.snapshot)
                                   for row in turn_outcomes))
            outcomes.append(runtime.RepeatOutcome(passed=passed, signature=signature))
    return runtime.classify_repeats(outcomes, case.risk).status


def _run_engine_direct(case, agents, builder, confirm_intents) -> list[TurnOutcome]:
    """完整入口的 Edge 对照臂：同一条 Engine 链，只是不经 Edge servicer。

    只有 cloud-direct 通过而完整入口失败时，首偏离点才是 Edge；反过来推不出
    「Edge 有问题」——那只说明这句话本来就不该上云。
    """
    sink = TraceSink()
    first = case.turns[0]
    clients = runtime.SafeClients(
        agents, history=list(first.context.get("history") or []),
        memories=list(first.context.get("memories") or []),
        confirm_intents=confirm_intents)
    harness = runtime.build_engine_harness(builder, agents, clients, sink)
    session_id = f"adv-direct-{case.id}"
    if first.context.get("focus"):
        harness.seed_focus(session_id, dict(first.context["focus"]))
    pending = first.context.get("pending_confirm") or {}
    if pending:
        harness.seed_pending_confirm(session_id, intent=str(pending.get("intent") or ""),
                                     step_id=str(pending.get("step_id") or "s1"))
    outcomes: list[TurnOutcome] = []
    with probe_builder(builder, sink):
        for turn in case.turns:
            before = (len(sink.validations), len(sink.hints), len(sink.plans))
            engine = harness.run(
                turn.utterance, session_id=session_id,
                is_confirmation=bool(turn.context.get("is_confirmation")),
                meta={k: str(v) for k, v in (turn.context.get("meta") or {}).items()})
            plans = [trace.plan for trace in sink.plans[before[2]:]]
            snapshot = DecisionSnapshot(
                ingress="cloud", addressed=True, decision=engine.decision,
                clarify=engine.decision == "clarify",
                plan=plans[0] if plans else PlanSnapshot.empty(),
                replans=tuple(plans[1:]),
                side_effects=_engine_side_effects(engine),
                engine_observed=True,
                agent_calls=tuple(row["intent"] for row in engine.agent_calls),
                pending_confirm_after=engine.pending_confirm_after)
            expected = replace(turn.expected, ingress_allowed=(), ingress_forbidden=())
            outcomes.append(_turn_outcome(turn, snapshot,
                                          judge_turn(expected, snapshot),
                                          sink, before[:2]))
    return outcomes


if __name__ == "__main__":
    raise SystemExit(main())
