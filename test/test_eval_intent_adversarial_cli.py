"""CLI 参数、退出码、baseline 硬闸与 L3 子进程的回归测试。"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

import eval_intent_adversarial as cli  # noqa: E402
from eval_intent_adversarial import (  # noqa: E402
    known_journey_ids, load_journey_links, parse_args, read_l3_report,
    repro_command, run_l3, select_cases, validate_args, write_baseline_if_eligible,
)
from support.intent_adversarial_contract import (  # noqa: E402
    AdversarialCase, CaseTurn, IntentGroup, PlanExpectation, RelationSpec,
    SuiteConfig, TurnExpectation,
)
from support.intent_adversarial_report import BaselineEligibility  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _suite(statuses, live_statuses):
    return SuiteConfig(statuses=statuses, live_statuses=live_statuses,
                       min_cases=1, max_cases=999, attack_minimums={},
                       normal_repeats=1, failure_repeats=3, high_risk_repeats=3)


def _case(case_id, *, status="reviewed", layers=("l0", "l1"), tags=None, risk="medium",
          cohort="unseen_transfer", turns=None, relation=None, family=None):
    merged = {"attacks": ["A1"], "layers": list(layers)}
    merged.update(tags or {})
    return AdversarialCase(
        id=case_id, title=case_id, family_id=family or case_id, cohort=cohort,
        risk=risk, status=status, tags=merged, provenance={"kind": "authored"},
        turns=tuple(turns or (CaseTurn(utterance="查天气", context={},
                                       expected=TurnExpectation()),)),
        relation=relation)


def test_default_is_offline_l0_discovery():
    args = parse_args([])
    assert args.suite == "discovery"
    assert args.layer == "l0"
    assert args.live is False
    assert args.retrieval_state == "warm"


def test_l1_l2_require_live():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l1"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--layer", "l1", "--live", "--provider", "mimo"]))
    validate_args(parse_args([
        "--layer", "l1", "--live", "--provider", "mimo",
        "--model", "mimo-model"]))


def test_write_baseline_requires_gate_and_explicit_provider():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "discovery"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "gate", "--live"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--write-baseline", "--suite", "gate", "--live",
            "--provider", "mimo", "--model", "mimo-model",
            "--layer", "l1"]))


def test_cold_start_never_reaches_gate():
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--suite", "gate", "--retrieval-state", "cold", "--layer", "l1",
            "--live", "--provider", "mimo", "--model", "m"]))


def test_cli_has_no_bypass_flags():
    for flag in ("--update-baseline", "--accept-failures", "--force"):
        with pytest.raises(SystemExit):
            parse_args([flag])


# ── P0-1 反向构造：正常参数组合构成的等价 --force ────────────────────────


def _baseline_argv(*extra):
    return ["--write-baseline", "--suite", "gate", "--layer", "all", "--live",
            "--provider", "mimo", "--model", "m", *extra]


@pytest.mark.parametrize("extra", [
    ["--case", "only.one"],
    ["--tag", "A7"],
    ["--cohort", "seen_regression"],
    ["--risk", "low"],
    ["--repeat", "1"],
])
def test_write_baseline_rejects_every_selection_and_repeat_override(extra):
    """一条通过的 stable case 加一条 L3 链接就能覆盖正式基线——CLI 里虽然没有
    `--force`，现有正常过滤参数已经形成等价绕过。`--repeat 1` 还能把高风险三次策略
    降成一次。"""
    with pytest.raises(SystemExit) as exc:
        validate_args(parse_args(_baseline_argv(*extra)))
    assert exc.value.code == 2


def test_write_baseline_still_accepts_a_clean_full_run():
    args = validate_args(parse_args(_baseline_argv()))
    assert cli.selection_filters(args) == []


def test_single_green_case_cannot_write_the_formal_baseline(tmp_path, monkeypatch):
    """突变测试：把执行器换成「只产出一条全绿证据」，正式基线必须一个字节都不变。"""
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    formal_json.write_text("old-json", encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")
    monkeypatch.setattr(cli, "FORMAL_BASELINE_JSON", formal_json)
    monkeypatch.setattr(cli, "FORMAL_BASELINE_MD", formal_md)

    green = _green_result("only.one@l0")
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: ([green], []))
    monkeypatch.setattr(cli, "_l3_evidence", lambda *a, **k: ([], {}, [], {}))
    monkeypatch.setattr(cli, "_l3_results", lambda *a, **k: [])
    monkeypatch.setattr(cli.eval_live, "load_agents", lambda **k: [])
    monkeypatch.setattr(cli.runtime, "confirm_intent_inventory", lambda agents: set())
    monkeypatch.setattr(cli.eval_live, "make_builder", lambda *a, **k: object())
    monkeypatch.setattr(cli.eval_common, "ProviderLock", _FakeLock)
    monkeypatch.setattr(cli, "_semantic_retrieval_expected", lambda: False)
    monkeypatch.setattr(cli.asyncio, "run", lambda *a, **k: 1)
    monkeypatch.chdir(tmp_path)

    code = cli.main(_baseline_argv())

    assert code == 2
    assert formal_json.read_text(encoding="utf-8") == "old-json"
    assert formal_md.read_text(encoding="utf-8") == "old-md"


class _FakeLock:
    locked = True
    drifts = ()

    def __init__(self, *_args, **_kwargs):
        pass

    def pin(self):
        return "mimo:m"

    def check(self, *_args, **_kwargs):
        return None

    def restore(self):
        return "mimo:m"

    def summary(self):
        return {"locked": True}


def _green_result(unit):
    from support.intent_adversarial_report import AdversarialResult
    case_id, _, layer = unit.partition("@")
    return AdversarialResult(
        result_id=unit, case_id=case_id, layer=layer, title=case_id, passed=True,
        repeat_status="pass", cohort="unseen_transfer", risk="low", status="stable",
        provenance_kind="authored", provider_model="mimo:m",
        dimensions={"expected_intent": ("info.weather",),
                    "expected_domain": ("info",), "actual_intent": ("info.weather",),
                    "actual_domain": ("info",), "boundary": (), "attack": ("A1",),
                    "risk": ("low",), "ingress": ("cloud",),
                    "cohort": ("unseen_transfer",), "layer": (layer,),
                    "provider": ("mimo:m",), "status": ("stable",),
                    "provenance": ("authored",)},
        metrics={"exact_plan_set": 1.0}, expected={}, actual={},
        admitted_intents=("info.weather",), actual_intents=("info.weather",),
        assertions=(), repetitions=({"passed": True},))


def test_candidates_never_reach_a_live_layer():
    cases = [_case("cand", status="candidate"), _case("rev", status="reviewed")]
    suite = _suite(("candidate", "reviewed", "stable"), ("reviewed", "stable"))
    offline = select_cases(cases, parse_args(["--layer", "l0"]), suite)
    live = select_cases(cases, parse_args([
        "--layer", "l1", "--live", "--provider", "p", "--model", "m"]), suite)
    assert {c.id for c in offline} == {"cand", "rev"}
    assert {c.id for c in live} == {"rev"}


def test_tag_filter_matches_values_and_truthy_keys():
    cases = [_case("a", tags={"attacks": ["A7"], "gate_candidate": True}),
             _case("b", tags={"attacks": ["A1"]})]
    suite = _suite(("reviewed",), ("reviewed",))
    assert {c.id for c in select_cases(
        cases, parse_args(["--tag", "A7"]), suite)} == {"a"}
    assert {c.id for c in select_cases(
        cases, parse_args(["--tag", "gate_candidate"]), suite)} == {"a"}
    assert select_cases(
        cases, parse_args(["--tag", "A7", "--tag", "A1"]), suite) == []


def test_l3_uses_existing_runner_and_selected_journey_ids(monkeypatch):
    called = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: called.update(
        argv=argv, env=kw["env"]) or SimpleNamespace(returncode=0))
    assert run_l3(
        ["A1-1", "B4-1"], provider="mimo", model="mimo-model") == 0
    assert called["argv"] == [sys.executable, "scripts/run_e2e.py", "--id",
                              "e2e_journeys", "--provider", "mimo",
                              "--model", "mimo-model"]
    assert called["env"]["E2E_JOURNEY_IDS"] == "A1-1,B4-1"


def test_journey_links_reject_unknown_journey_ids(tmp_path):
    path = tmp_path / "journey_links.yaml"
    path.write_text("schema_version: 1\nlinks:\n  some.case: [NOPE-9]\n",
                    encoding="utf-8")
    with pytest.raises(ValueError, match="unknown journeys"):
        load_journey_links(path)


def test_known_journey_ids_reads_the_real_corpus():
    ids = known_journey_ids()
    assert "A1-1" in ids and len(ids) > 10


def test_ineligible_run_never_touches_formal_baseline(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    rejected_json = tmp_path / "_ci-run-rejected.json"
    rejected_md = tmp_path / "_ci-run-rejected.md"
    formal_json.write_text("old-json", encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")

    written = write_baseline_if_eligible(
        {"meta": {}}, "diagnostic", BaselineEligibility(False, ("l3_empty",)),
        formal_json, formal_md, rejected_json, rejected_md)

    assert written is False
    assert formal_json.read_text(encoding="utf-8") == "old-json"
    assert formal_md.read_text(encoding="utf-8") == "old-md"
    assert rejected_json.is_file() and rejected_md.is_file()


def test_eligible_run_writes_the_formal_pair(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    written = write_baseline_if_eligible(
        {"meta": {}}, "official", BaselineEligibility(True, ()),
        formal_json, formal_md, tmp_path / "r.json", tmp_path / "r.md")
    assert written is True
    assert formal_md.read_text(encoding="utf-8") == "official"


def test_list_never_requires_live_because_it_runs_no_model():
    for layer in ("l1", "l2", "l3", "all"):
        validate_args(parse_args(["--layer", layer, "--list"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l3"]))


def test_journey_links_resolve_against_the_real_journey_corpus():
    links = load_journey_links()
    assert links, "L3 选集为空时 baseline 会被 l3_empty 拒掉，链接不能是空的"
    known = known_journey_ids()
    for case_id, journeys in links.items():
        assert journeys, f"{case_id} 链接了空 journey 列表"
        for journey in journeys:
            assert journey in known


# ── P0-3 反向构造：第二轮不许被静默忽略 ─────────────────────────────────


def _two_turn_case():
    """第二轮的 gold 与第一轮**相反**：执行器只跑第一轮时，这条 case 会整条通过。"""
    first = CaseTurn(utterance="打开空调", context={},
                     expected=TurnExpectation(ingress_allowed=("edge_local",)))
    second = CaseTurn(utterance="打开空调", context={},
                      expected=TurnExpectation(ingress_allowed=("cloud",)))
    return _case("mt.opposite", layers=("l0",), turns=(first, second))


def test_every_declared_turn_is_executed_and_judged():
    outcomes = cli.run_l0_case(_two_turn_case())
    assert len(outcomes) == 2, "契约声明了两轮，执行器必须跑两轮"
    assert outcomes[0].judgement.passed is True
    assert outcomes[1].judgement.passed is False, "第二轮相反 gold 必须红"


def test_multi_turn_becomes_one_evidence_unit_per_turn():
    case = _two_turn_case()
    args = parse_args(["--layer", "l0"])
    units = cli._expected_units([case], args)
    assert units == {"mt.opposite#1@l0", "mt.opposite#2@l0"}
    # 单轮沿用 case_id@layer，既有报告键不变
    assert cli._expected_units([_case("single", layers=("l0",))], args) == {
        "single@l0"}


def test_multi_turn_shares_one_edge_session_so_state_carries():
    """同 session 顺序执行：第二轮看得见第一轮改过的 VAL 状态。"""
    from support.intent_adversarial_runtime import EdgeSession

    session = EdgeSession()
    session.turn("打开空调")
    assert session.srv.val.state.get("hvac_on") is True
    second = session.turn("关掉空调")
    assert second.state_delta.get("hvac_on") is False


# ── P0-2 反向构造：L2 快照必须同时带 Edge 与 Engine 证据 ────────────────


def _l2_case(case_id, utterances, expected_list, *, risk="critical"):
    turns = tuple(CaseTurn(utterance=u, context={}, expected=e)
                  for u, e in zip(utterances, expected_list))
    return _case(case_id, layers=("l2",), risk=risk, turns=turns)


class _ScriptedBuilder:
    """只产计划，不带 `_parse_and_validate_data`——即「没有 raw 通道」的 builder。"""

    def __init__(self, intent):
        from orchestrator.cloud.models import Plan, Step
        self._plan = Plan(steps=[Step(id="s1", agent_id=intent.split(".", 1)[0],
                                      endpoint="fake:1", intent=intent)],
                          complexity="simple", goal="完成用户目标")
        self._route_hints = SimpleNamespace(
            apply=lambda plan, text, agent_map: False)

    async def build(self, *_args, **_kwargs):
        from copy import deepcopy
        return deepcopy(self._plan)

    async def replan(self, *_args, **_kwargs):
        from orchestrator.cloud.models import ReplanDecision
        return ReplanDecision(done=True)


def test_l2_snapshot_carries_agent_calls_pending_state_and_both_side_effects():
    from support.intent_adversarial_contract import EngineExpectation

    expected = TurnExpectation(
        decision_allowed=("confirm",),
        engine=EngineExpectation(declared=True, pending_confirm_after=True,
                                 forbidden_agent_calls=("parking.pay",),
                                 max_agent_calls_per_intent=1),
        no_side_effect_before_confirm=True)
    case = _l2_case("l2.smoke", ["帮我订一份宫保鸡丁"], [expected])
    outcomes = cli.run_l2_case(case, [], _ScriptedBuilder("nearby.order"),
                               {"nearby.order", "parking.pay"})

    assert len(outcomes) == 1
    snapshot = outcomes[0].snapshot
    assert snapshot.engine_observed is True
    assert snapshot.agent_calls == ("nearby.order",)
    assert snapshot.pending_confirm_after is True
    assert snapshot.side_effects == (), "确认前不得有副作用"
    assert outcomes[0].judgement.passed is True
    assert outcomes[0].raw_observed is False, "脚本 builder 没有 raw 通道，不进幻觉率分母"


def test_l2_catches_an_agent_that_was_called_but_should_not_have_been():
    """反向构造：计划落到 parking.pay，而 gold 说这一句不该碰它。

    只有 `no_side_effect_before_confirm` 时这条是绿的——确认闸拦住了执行，
    但**该 Agent 已经被够着了**，副作用面看不见这件事。
    """
    from support.intent_adversarial_contract import EngineExpectation

    expected = TurnExpectation(
        engine=EngineExpectation(declared=True,
                                 forbidden_agent_calls=("parking.pay",)),
        no_side_effect_before_confirm=True)
    case = _l2_case("l2.wrong-agent", ["帮我订一份宫保鸡丁"], [expected])
    outcomes = cli.run_l2_case(case, [], _ScriptedBuilder("parking.pay"),
                               {"nearby.order", "parking.pay"})

    judgement = outcomes[0].judgement
    assert outcomes[0].snapshot.side_effects == (), "确认闸确实拦住了执行"
    assert not judgement.passed
    failed = {a.name for a in judgement.assertions if not a.passed}
    assert "engine.forbidden_agent_calls" in failed


def test_l2_multi_turn_shares_one_session_and_counts_repeat_execution():
    from support.intent_adversarial_contract import EngineExpectation

    expected = TurnExpectation(
        engine=EngineExpectation(declared=True, max_agent_calls_per_intent=1),
        no_side_effect_before_confirm=True)
    case = _l2_case("l2.repeat", ["交一下停车费", "交一下停车费"],
                    [expected, expected])
    outcomes = cli.run_l2_case(case, [], _ScriptedBuilder("parking.pay"),
                               {"parking.pay"})

    assert len(outcomes) == 2, "两轮都要执行"
    # SafeClients.agent_calls 跨轮累积：第二轮看得见第一轮调过一次
    assert outcomes[1].snapshot.agent_calls == ("parking.pay", "parking.pay")
    failed = {a.name for a in outcomes[1].judgement.assertions if not a.passed}
    assert "engine.max_agent_calls_per_intent" in failed, \
        "单轮版本里这条断言恒真——只有第二轮存在时它才可证"


# ── P1-4 反向构造：raw / pre-hint 证据必须来自主入口，而不是单测 ────────


def test_l1_main_entry_records_raw_candidate_and_pre_hint_plan():
    """`attach_validation_trace()` 与 `TracingRouteHints` 原来只在单测里被调用过。

    主 CLI 从不消费它们，于是「每个 live 失败都有首偏离点」退化成「一律记
    PLANNER_DIVERGENCE」——连 L0（根本没有 Planner）的 5 条确定性失败都被这么标了。
    """
    import asyncio

    import eval_live
    from orchestrator.cloud.planning import PlanBuilder

    async def llm(_messages):
        return json.dumps({"goal": "查天气", "complexity": "simple", "steps": [
            {"id": "s1", "intent": "info.weather", "slots": {}},
            {"id": "s2", "intent": "does.not_exist", "slots": {}}]})

    async def tool_llm(_messages, _tools):
        return "", []

    builder = PlanBuilder(llm_fn=llm, registry_fn=None, llm_tool_fn=tool_llm)
    case = _case("trace.raw", layers=("l1",), turns=(CaseTurn(
        utterance="今天天气怎么样", context={},
        expected=TurnExpectation(plan=PlanExpectation(
            assert_plan=True,
            required_groups=(IntentGroup(("info.weather",)),)))),))

    outcomes = asyncio.run(cli.run_l1_case(
        case, eval_live.load_agents(include_edge=True), builder))

    assert outcomes[0].raw_observed is True
    assert "does.not_exist" in outcomes[0].raw_intents, \
        "校验前的候选里有编出来的能力——validator 删掉它之后就再也看不见了"
    assert "does.not_exist" not in outcomes[0].snapshot.plan.intents
    assert outcomes[0].pre_hint_pass is not None, "Hint 前计划必须留证"


# ── P1-5 反向构造：relation 与可执行复现命令 ────────────────────────────


def test_selecting_a_variant_pulls_in_its_relation_base():
    base = _case("rel.base", family="rel")
    variant = _case("rel.variant", family="rel",
                    relation=RelationSpec("rel.base", "invariant", {}))
    suite = _suite(("reviewed",), ("reviewed",))
    picked = select_cases([base, variant], parse_args(["--case", "rel.variant"]),
                          suite)
    assert {c.id for c in picked} == {"rel.base", "rel.variant"}, \
        "只选 variant 时 relation 断言根本不会被裁——复现命令复现不出报告里的失败"


def test_relation_pair_repeats_are_lifted_to_the_same_count():
    base = _case("rel.base", family="rel", risk="low")
    variant = _case("rel.variant", family="rel", risk="high",
                    relation=RelationSpec("rel.base", "invariant", {}))
    suite = _suite(("reviewed",), ("reviewed",))
    plan = cli.repeat_plan([base, variant], parse_args(["--layer", "l0"]), suite)
    assert plan["rel.base"] == plan["rel.variant"] == 3, \
        "逐次成对裁 relation 时两边次数必须一致，否则得拿第 1 次凑第 3 次"


def _snapshot(intents):
    from support.intent_adversarial_judge import (
        DecisionSnapshot, PlanSnapshot, StepSnapshot,
    )
    steps = tuple(StepSnapshot(id=f"s{i}", agent_id=intent.split(".", 1)[0],
                               intent=intent, slots={}, depends_on=(),
                               slot_refs={}, require_confirm=False)
                  for i, intent in enumerate(intents, 1))
    return DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=steps, complexity="simple", goal="g",
                          skills=(), exemplars=(), hint_effect="",
                          catalog_stats={}))


def _outcome_for(intents, expectation):
    from support.intent_adversarial_judge import judge_turn
    snapshot = _snapshot(intents)
    return cli.TurnOutcome(snapshot=snapshot,
                           judgement=judge_turn(expectation, snapshot))


def test_relation_failure_reaches_the_repeat_classification():
    """反向构造 P1-5：绝对 gold 三次全过，但 invariant 三次都不成立。

    relation 原来在重复分类**之后**才追加，只改 `passed` 不改 `repeat_status`——
    第二趟产物里因此出现过 3 条 `passed=false` 但 `repeat_status=pass` 的自相矛盾行。
    """
    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),),
        allow_extra_intents=True))
    base = _case("rel.base", layers=("l1",), family="rel", risk="high")
    variant = _case("rel.variant", layers=("l1",), family="rel", risk="high",
                    relation=RelationSpec("rel.base", "invariant", {}))
    for case in (base, variant):
        object.__setattr__(case, "turns", (CaseTurn(
            utterance="今天天气怎么样", context={}, expected=expectation),))

    units = {
        "rel.base@l1": cli.UnitRuns(case=base, layer="l1", runs=[
            [_outcome_for(("info.weather",), expectation)] for _ in range(3)]),
        # variant 每次都多规划一步：绝对 gold 放行 extra，但 invariant 必须红
        "rel.variant@l1": cli.UnitRuns(case=variant, layer="l1", runs=[
            [_outcome_for(("info.weather", "nearby.search"), expectation)]
            for _ in range(3)]),
    }
    args = parse_args(["--layer", "l1", "--live", "--provider", "p", "--model", "m"])
    judgements, gaps = cli._relation_judgements(units, args)
    assert gaps == []
    assert len(judgements["rel.variant@l1"]) == 3, "每次 repetition 都要成对裁"

    rows = cli._assemble_unit(units["rel.variant@l1"], args, [], None, set(),
                              "p:m", judgements["rel.variant@l1"])
    assert len(rows) == 1
    assert rows[0].passed is False
    assert rows[0].repeat_status == "stable_fail", \
        "relation 失败必须进重复分类，不能留下 passed=false / repeat_status=pass"
    assert rows[0].metrics["relation_pass"] == 0.0


def test_missing_relation_base_is_reported_not_silently_skipped():
    """反向构造 P1-5：对照跑挂了。

    静默跳过会让 variant 拿着**少裁一条 gold** 的判定通过——这与 `--case <variant>`
    找不到 base 时直接 continue 是同一个洞。
    """
    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    variant = _case("rel.variant", layers=("l1",), family="rel",
                    relation=RelationSpec("rel.base", "invariant", {}))
    object.__setattr__(variant, "turns", (CaseTurn(
        utterance="今天天气怎么样", context={}, expected=expectation),))
    units = {"rel.variant@l1": cli.UnitRuns(
        case=variant, layer="l1",
        runs=[[_outcome_for(("info.weather",), expectation)]])}
    args = parse_args(["--layer", "l1", "--live", "--provider", "p", "--model", "m"])

    judgements, gaps = cli._relation_judgements(units, args)

    assert judgements == {}
    assert any("relation_base_missing" in row for row in gaps)


def test_repro_command_is_argparse_valid_and_carries_provider_and_base():
    variant = _case("rel.variant", layers=("l1", "l2"), family="rel",
                    relation=RelationSpec("rel.base", "invariant", {}))
    args = parse_args(["--layer", "all", "--live", "--provider", "minimax",
                       "--model", "MiniMax-M3"])
    command = repro_command(variant, "l1", args)
    assert "--layer l1" in command and "l1/l2" not in command
    assert "--live --provider minimax --model MiniMax-M3" in command
    assert "--case rel.variant --case rel.base" in command
    # 生成的命令必须能被同一个 parser 接受
    argv = command.split()[2:]
    parsed = validate_args(parse_args(argv))
    assert parsed.cases == ["rel.variant", "rel.base"] and parsed.diagnose is True


def test_repro_command_for_an_l0_case_actually_runs(tmp_path):
    """复现命令要真能跑：子进程执行一条真实 L0 用例并检查退出码与选集。"""
    case = _case("ki.weather-outing.miss", layers=("l0",))
    command = repro_command(case, "l0", parse_args(["--layer", "l0"]))
    argv = command.split()[1:] + [
        "--out-json", str(tmp_path / "r.json"), "--out-md", str(tmp_path / "r.md")]
    proc = subprocess.run([sys.executable, *argv], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert set(report["results"]) == {"ki.weather-outing.miss@l0"}
    assert "首偏离" in proc.stdout, "--diagnose 必须有消费方"


def test_empty_selection_is_an_error_not_a_green_run(tmp_path):
    """`--case <打错的 id>` 原来跑完 0 条然后 exit=0——自动化读到的是「全过」。"""
    proc = subprocess.run(
        [sys.executable, "test/eval_intent_adversarial.py", "--layer", "l0",
         "--case", "no.such.case",
         "--out-json", str(tmp_path / "e.json"), "--out-md", str(tmp_path / "e.md")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "选集为空" in proc.stdout + proc.stderr


# ── P1-6 反向构造：旧 L3 报告冒充本次证据 ───────────────────────────────


def test_stale_l3_report_is_never_counted_as_this_run(tmp_path):
    import time

    report = tmp_path / "run" / "journeys_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"journeys": [{"id": "A1-1", "status": "pass"}]}),
                      encoding="utf-8")
    old = time.time() - 3600
    import os
    os.utime(report, (old, old))

    fresh, stale = read_l3_report(tmp_path, since=time.time() - 60)
    assert fresh == {} and stale, "目录里曾经成功过一次，之后的失败运行就能读到那份旧 pass"
    anytime, _ = read_l3_report(tmp_path)
    assert anytime == {"A1-1": "pass"}


def test_l3_runner_nonzero_exit_is_infrastructure_even_with_a_readable_report(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_journey_links", lambda *a, **k: {"c1": ["A1-1"]})

    def _fake_run_l3(ids, *, provider, model, artifact_root=None, env=None):
        Path(artifact_root).mkdir(parents=True, exist_ok=True)
        (Path(artifact_root) / "journeys_report.json").write_text(
            json.dumps({"journeys": [{"id": "A1-1", "status": "pass"}]}),
            encoding="utf-8")
        return 2

    monkeypatch.setattr(cli, "run_l3", _fake_run_l3)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    case = _case("c1", layers=("l3",))
    args = parse_args(["--layer", "l3", "--live", "--provider", "p", "--model", "m"])

    ids, statuses, infra, meta = cli._l3_evidence([case], args, "p:m")

    assert ids == ["A1-1"] and statuses == {"A1-1": "pass"}
    assert any("l3_runner_failed" in row for row in infra)
    assert meta["fresh"] is False and meta["exit_code"] == 2
    assert meta["invocation_id"] and meta["artifact_root"].endswith(
        meta["invocation_id"])


def test_l3_uses_a_unique_run_directory_per_invocation():
    first = cli.l3_invocation_id("abc1234")
    second = cli.l3_invocation_id("abc1234")
    assert first != second and "abc1234" in first


# ── P2-1/P2-2 反向构造：退出码与跨盘输出 ────────────────────────────────


def test_invalid_arguments_exit_with_code_two_not_one():
    """`SystemExit(<字符串>)` 的进程码是 1；模块头声明参数错误是 2。

    自动化会把「命令根本无效」记成「产品语义失败」。
    """
    proc = subprocess.run(
        [sys.executable, "test/eval_intent_adversarial.py", "--layer", "l1"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "需要 --live" in proc.stdout + proc.stderr


def test_out_of_tree_output_path_never_raises():
    """跨盘（repo 在 D:、输出在 C:）时 `os.path.relpath` 会抛 ValueError，
    而它在整跑结束后的 meta 组装里——L0 跑完 70 条才 traceback。"""
    assert cli.repo_relative(ROOT / "test" / "x.json") == "test/x.json"
    assert cli.repo_relative("//other-drive/tmp/x.json") is None
    # 仓库外的路径一律 None（不抛），`_worktree_clean` 的 ignore 集据此只收有效项
    assert cli.repo_relative(ROOT.parent / "outside.json") is None


def test_l2_case_path_never_nests_event_loops():
    """守住 `cases=0` 那个坑：L2 一条 case 要经 seed → Edge servicer → Engine 三段，
    每段都自己驱动事件循环。任何一段被包进 async 再 asyncio.run 就整层抛，
    而调用方会把它吞成基础设施错误——报告看起来像「没有可跑的用例」。"""
    import inspect

    import eval_intent_adversarial as cli
    from support import intent_adversarial_runtime as rt

    assert not inspect.iscoroutinefunction(cli.run_l2_case)
    for name in ("run_full_entry_turn", "run_edge_turn", "run_retrieval_turn"):
        assert not inspect.iscoroutinefunction(getattr(rt, name)), name
    for name in ("seed_pending_confirm", "seed_focus", "run"):
        assert not inspect.iscoroutinefunction(getattr(rt.EngineHarness, name)), name
    # 反过来，L1 是纯 Planner 调用，必须是 async（由调用方 asyncio.run 驱动）
    assert inspect.iscoroutinefunction(cli.run_l1_case)
    assert inspect.iscoroutinefunction(rt.EngineHarness.run_async)
