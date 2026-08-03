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
    """突变测试：把执行器换成「只产出一条全绿证据」，正式基线必须一个字节都不变。

    ⚠ **这条测试自己被审计过一次并修过**（2026-08-03 第二批）。旧版把
    `eval_live.load_agents` 换成空清单，于是 `coverage_exemptions.yaml` 里的意图
    在能力面上找不到，**整跑在契约校验那一层就 exit 2 了，根本没走到资格闸**——
    而契约错误的退出码与本条要证的东西恰好相同，两条断言（code==2、文件未变）
    因此全都恒真。实测 `write_baseline_if_eligible` 一次都没被调到。

    修法是**不替换 `load_agents`**（用真实 manifest 走完契约），并显式断言
    「资格闸真的被问过、且给出了非空理由」。守红线的测试自己要被审计，这是第三例。
    """
    # 占位内容必须是**合法 JSON**：`--write-baseline` 现在强制以正式基线自身为比较源
    # （见 P0-A），拿不可解析的占位会让 `load_baseline` 抢在闸前面炸。
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    formal_json.write_text('{"cases": {}, "_placeholder": "old"}', encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")
    monkeypatch.setattr(cli, "FORMAL_BASELINE_JSON", formal_json)
    monkeypatch.setattr(cli, "FORMAL_BASELINE_MD", formal_md)

    asked: dict = {}
    real_writer = cli.write_baseline_if_eligible

    def _spy(report, markdown, eligibility, *rest, **kw):
        asked["reasons"] = eligibility.reasons
        return real_writer(report, markdown, eligibility, *rest, **kw)

    async def _warm():
        return 0

    green = _green_result("only.one@l0")
    monkeypatch.setattr(cli, "write_baseline_if_eligible", _spy)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: ([green], []))
    monkeypatch.setattr(cli, "_l3_evidence", lambda *a, **k: ([], {}, [], {}))
    monkeypatch.setattr(cli, "_l3_results", lambda *a, **k: [])
    monkeypatch.setattr(cli.eval_live, "warm_exemplars", _warm)
    monkeypatch.setattr(cli.eval_live, "make_builder", lambda *a, **k: object())
    monkeypatch.setattr(cli.eval_common, "ProviderLock", _FakeLock)
    monkeypatch.setattr(cli, "_semantic_retrieval_expected", lambda: False)
    monkeypatch.chdir(tmp_path)

    code = cli.main(_baseline_argv())

    assert "reasons" in asked, "资格闸压根没被问到——这一跑在更早的地方就退出了"
    assert "declared_set_incomplete" in asked["reasons"]
    assert code == 2
    assert formal_json.read_text(encoding="utf-8") == '{"cases": {}, "_placeholder": "old"}'
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

    fresh, stale, _ = read_l3_report(tmp_path, since=time.time() - 60)
    assert fresh == {} and stale, "目录里曾经成功过一次，之后的失败运行就能读到那份旧 pass"
    anytime, _, _ = read_l3_report(tmp_path)
    assert anytime == {"A1-1": "pass"}


def test_l3_runner_nonzero_exit_is_infrastructure_even_with_a_readable_report(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_journey_links", lambda *a, **k: {"c1": ["A1-1"]})

    def _fake_run_l3(ids, *, provider, model, artifact_root=None, env=None):
        Path(artifact_root).mkdir(parents=True, exist_ok=True)
        (Path(artifact_root) / "journeys_report.json").write_text(
            json.dumps({"provider": "p:m", "run_id": "e2e-1",
                        "provider_lock": {"locked": True, "drift_detected": False},
                        "journeys": [{"id": "A1-1", "status": "pass"}]}),
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


# ── 兜底计划与检索降级（2026-08-03 第二批尺子硬化） ─────────────────────────


def _stub_the_live_scaffolding(monkeypatch, execute, *, semantic=True):
    """把主入口的外部依赖全部换成替身，只留下要被反向构造的那一条路径。"""
    import asyncio as _asyncio

    async def _warm():
        return 223                     # 预热**成功**——这正是本条要打的假象

    # **不替换 `load_agents`**：契约校验拿真实 manifest 比对能力面，替成空清单会在
    # 契约那一层就 exit 2——退出码与本条要证的东西撞车，测试会因为错误的理由变绿。
    monkeypatch.setattr(cli, "_execute", execute)
    monkeypatch.setattr(cli, "_l3_evidence", lambda *a, **k: ([], {}, [], {}))
    monkeypatch.setattr(cli, "_l3_results", lambda *a, **k: [])
    monkeypatch.setattr(cli.eval_live, "warm_exemplars", _warm)
    monkeypatch.setattr(cli.eval_live, "make_builder", lambda *a, **k: object())
    monkeypatch.setattr(cli.eval_common, "ProviderLock", _FakeLock)
    monkeypatch.setattr(cli, "_semantic_retrieval_expected", lambda: semantic)
    return _asyncio


def _live_argv(tmp_path, *extra):
    return ["--suite", "discovery", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m",
            "--out-json", str(tmp_path / "run.json"),
            "--out-md", str(tmp_path / "run.md"), *extra]


def test_mid_run_retrieval_degradation_is_infrastructure_not_a_reading(
        tmp_path, monkeypatch):
    """反向构造：**预热成功**、逐轮检索却掉档。

    宿主实测形态：`EXEMPLAR_EMBED_TIMEOUT` 缺省 1.0s 而一次 Embed 要 0.27–1.12s，
    首次调用超时 → 30s 失败冷却 → 其后整段规划只跑词法档。预热用的是
    `max(5.0, timeout)`，它成功了，于是报告照写 `retrieval_state=warm`。
    旧口径下这一跑照样出数、退出 0。
    """
    from orchestrator.cloud import embedding

    async def _degraded(_texts, _timeout_s=1.0):
        return None

    monkeypatch.setattr(embedding, "embed_texts", _degraded)

    def _execute_losing_the_semantic_channel(*_a, **_k):
        asyncio = __import__("asyncio")
        asyncio.run(embedding.embed_texts(["附近的充电站"]))
        return ([_green_result("only.one@l1")], [])

    _stub_the_live_scaffolding(monkeypatch, _execute_losing_the_semantic_channel)
    monkeypatch.chdir(tmp_path)

    code = cli.main(_live_argv(tmp_path))

    assert code == 2
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert report["meta"]["warmed_exemplars"] == 223      # 预热确实是「成功」的
    assert report["meta"]["retrieval_degraded"] == 1
    assert any("retrieval_degraded_mid_run" in row
               for row in report["meta"]["infrastructure_errors"])


def test_a_healthy_semantic_channel_is_not_reported_as_degraded(tmp_path, monkeypatch):
    """反向构造的另一半：通道正常时这条闸不许误伤，否则它只是一个永远红的装饰。"""
    from orchestrator.cloud import embedding

    async def _healthy(texts, _timeout_s=1.0):
        return ([(1.0,)] * len(texts), "text-embedding-v4")

    monkeypatch.setattr(embedding, "embed_texts", _healthy)

    def _execute(*_a, **_k):
        asyncio = __import__("asyncio")
        asyncio.run(embedding.embed_texts(["附近的充电站"]))
        return ([_green_result("only.one@l1")], [])

    _stub_the_live_scaffolding(monkeypatch, _execute)
    monkeypatch.chdir(tmp_path)

    assert cli.main(_live_argv(tmp_path)) == 0
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert (report["meta"]["retrieval_calls"],
            report["meta"]["retrieval_degraded"]) == (1, 0)
    assert report["meta"]["infrastructure_errors"] == []


def test_a_green_run_still_says_when_the_plan_came_from_the_fallback(
        tmp_path, monkeypatch):
    """通过 + 兜底计划：断言面不改判，但摘要与报告必须说出来，且写不进 baseline。"""
    from dataclasses import replace as _replace

    def _execute(*_a, **_k):
        return ([_replace(_green_result("nq.hvac-keep.dont@l1"),
                          plan_from_fallback=True)], [])

    _stub_the_live_scaffolding(monkeypatch, _execute, semantic=False)
    monkeypatch.chdir(tmp_path)

    assert cli.main(_live_argv(tmp_path)) == 0            # 断言面确实全绿
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert report["fallback_passes"] == ["nq.hvac-keep.dont@l1"]
    assert report["metrics"]["fallback_plan_rate"]["value"] == 1.0
    from support.intent_adversarial_report import baseline_eligibility
    assert report["unexpected_fallback_plans"] == ["nq.hvac-keep.dont@l1"]
    assert "unexpected_fallback_plans" in baseline_eligibility(report).reasons


def test_relation_only_failure_triggers_the_failure_expansion(monkeypatch):
    """反向构造：绝对 gold 三次都过、relation 三次都败的 medium variant。

    扩展原来只看绝对 gold，而 relation 裁决在它之后才发生：这类 variant 只跑 1 次，
    那一次红随后被分类成 `unstable`——既不进修复清单也不进门禁。143 条 relation
    variant 里 129 条是 low/medium，不是边角路径（评审 P1-C）。
    """
    runs = {"count": 0}

    def _run_once(case, layer):
        runs["count"] += 1
        return [SimpleNamespace(judgement=SimpleNamespace(passed=True))]

    base = _case("base", risk="medium", layers=("l1",))
    variant = _case("variant", risk="medium", layers=("l1",),
                    relation=RelationSpec(base_case="base", type="invariant",
                                          expectation="same_route"))
    units = {
        "base@l1": cli.UnitRuns(case=base, layer="l1", runs=[_run_once(base, "l1")]),
        "variant@l1": cli.UnitRuns(case=variant, layer="l1",
                                   runs=[_run_once(variant, "l1")]),
    }
    runs["count"] = 0
    suite = _suite({"reviewed"}, {"reviewed"})
    args = parse_args(["--layer", "l1", "--live", "--provider", "p", "--model", "m"])
    partners = {"variant": "base", "base": "variant"}
    relations = {"variant@l1": {0: SimpleNamespace(passed=False)}}

    cli._expand_failures(units, partners, suite, args, _run_once, [], relations)

    assert len(units["variant@l1"].runs) == suite.failure_repeats
    assert len(units["base@l1"].runs) == suite.failure_repeats, "成对的两条要同步扩"

    # 反向：relation 也通过时不许平白补跑
    units2 = {"variant@l1": cli.UnitRuns(case=variant, layer="l1",
                                         runs=[_run_once(variant, "l1")])}
    cli._expand_failures(units2, {}, suite, args, _run_once, [],
                         {"variant@l1": {0: SimpleNamespace(passed=True)}})
    assert len(units2["variant@l1"].runs) == 1


def test_l3_report_from_the_wrong_provider_is_not_this_run_s_evidence(tmp_path):
    """反向构造：报告是**新写的**，但档位不是本次要的那个。

    只校验 mtime 时，一份新鲜但错档 / 错选集的报告照样成为 L3 pass——调用方写进
    `meta.l3_invocation` 的 provider/选集只是「本次想跑什么」，不是对产物「实际
    跑了什么」的核对（评审 P1-D）。
    """
    report = tmp_path / "run" / "journeys_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "provider": "deepseek:deepseek-v4-flash", "run_id": "e2e-1",
        "provider_lock": {"locked": True, "drift_detected": False},
        "journeys": [{"id": "A1-1", "status": "pass"}]}), encoding="utf-8")

    statuses, _, identity = read_l3_report(tmp_path, expect_provider="minimax:MiniMax-M3")
    assert statuses == {}, "档位不同的报告不采信，不是「警告一下照样用」"
    assert any("l3_provider_mismatch" in row for row in identity)

    # 档位对上就照常读
    ok, _, clean = read_l3_report(tmp_path, expect_provider="deepseek:deepseek-v4-flash")
    assert ok == {"A1-1": "pass"} and clean == []


def test_l3_report_covering_journeys_we_never_selected_is_flagged(tmp_path):
    """runner 没按 `E2E_JOURNEY_IDS` 走时，读到的 pass 可能压根来自别的用例。"""
    report = tmp_path / "run" / "journeys_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "provider": "p:m", "run_id": "e2e-1",
        "provider_lock": {"locked": True, "drift_detected": False},
        "journeys": [{"id": "A1-1", "status": "pass"},
                     {"id": "B3-3", "status": "pass"}]}), encoding="utf-8")

    _, _, identity = read_l3_report(tmp_path, expect_provider="p:m",
                                    expect_ids=["A1-1"])
    assert any("l3_selection_mismatch" in row and "B3-3" in row for row in identity)
    _, _, clean = read_l3_report(tmp_path, expect_provider="p:m",
                                 expect_ids=["A1-1", "B3-3"])
    assert clean == []


def test_an_l3_case_without_a_journey_link_is_a_gap_not_a_disappearance():
    """反向构造：一条声明了 `layers: [l3]` 的 case，link 表里没有它。

    旧实现只给「有 link」的 case 建 L3 声明单元，于是它同时从 expected 与 produced
    两个集合里消失——完整性检查看不见，只要还剩另一条 L3，`l3_empty` 也不会拦。
    **声明了却没链接是缺口，不是「不存在」。**
    """
    linked = _case("linked", layers=("l3",))
    orphan = _case("orphan", layers=("l3",))
    args = parse_args(["--layer", "l3", "--live", "--provider", "p", "--model", "m"])
    units = cli._expected_units([linked, orphan], args)
    assert units == {"linked@l3", "orphan@l3"}


# ── 第三批复审（§9）的 2 P0 / 2 P1 ─────────────────────────────────────────


def test_write_baseline_refuses_a_custom_comparison_source():
    """反向构造：`--write-baseline --baseline missing.json`。

    主流程从 `--baseline` 读旧基线做逐例回退 / 删除案例 / gold 变化三道检查，却固定
    写 `FORMAL_BASELINE_JSON`——两者可以不是同一个文件，于是拿一份空基线做比较、
    三道检查全部落空，然后照样覆盖正式基线。**没有 `--force`，但正常参数拼得出来一个**
    （这已经是同一形态的第三条：选集过滤器 / `--repeat` / 比较源）。
    """
    with pytest.raises(SystemExit) as exc:
        validate_args(parse_args(_baseline_argv("--baseline", "missing.json")))
    assert exc.value.code == 2
    # 缺省（= 正式基线本身）仍然放行
    assert validate_args(parse_args(_baseline_argv())).baseline == str(
        cli.FORMAL_BASELINE_JSON)


def test_gold_digest_closes_over_every_field_the_judge_reads():
    """反向构造：只改一条 judge 真的会读、但第一版摘要漏掉的 gold 字段。

    手挑字段的摘要必然随契约扩张而漏——第一版就漏了 retrieval / addressed /
    assert_plan / dependencies / slots / 每轮 replan 的完整 plan gold，
    「只删一条 required skill 前后指纹逐字相同」。
    """
    from dataclasses import replace as _replace

    from support.intent_adversarial_contract import (
        DependencyExpectation, PlanExpectation, RetrievalExpectation,
        ReplanExpectation, SlotExpectation,
    )

    base = _case("g", turns=(CaseTurn(
        utterance="附近的充电站", context={},
        expected=TurnExpectation(
            addressed=True,
            plan=PlanExpectation(assert_plan=True,
                                 required_groups=(IntentGroup(("charging.find",)),)),
            retrieval=RetrievalExpectation(required_skills=("charging-route",)))),))
    original = cli.gold_digest(base, base.turns[0])

    def _mutated(**changes):
        turn = base.turns[0]
        return cli.gold_digest(
            base, _replace(turn, expected=_replace(turn.expected, **changes)))

    mutations = {
        "retrieval": _mutated(retrieval=RetrievalExpectation(required_skills=())),
        "addressed": _mutated(addressed=None),
        "assert_plan": _mutated(plan=_replace(base.turns[0].expected.plan,
                                              assert_plan=False)),
        "complexity": _mutated(plan=_replace(base.turns[0].expected.plan,
                                             allowed_complexities=("simple",))),
        "dependency": _mutated(plan=_replace(
            base.turns[0].expected.plan,
            dependencies=(DependencyExpectation(("a.b",), "c.d", ("x",)),))),
        "slot": _mutated(plan=_replace(
            base.turns[0].expected.plan,
            slots=(SlotExpectation("charging.find", "keyword", "presence"),))),
        "replan_gold": _mutated(replans=(ReplanExpectation(
            after={"result": {}},
            plan=PlanExpectation(assert_plan=True,
                                 required_groups=(IntentGroup(("nearby.search",)),))),)),
    }
    for name, digest in mutations.items():
        assert digest != original, f"改了 {name} 但 gold 指纹没变"
    assert len({*mutations.values()}) == len(mutations), "不同改动不该撞成同一个指纹"


def test_raw_evidence_binds_to_the_accepted_attempt_not_the_first_one():
    """反向构造：第一次候选错、第二次候选对且被接受。

    生产 `PlanBuilder.build()` 最多两次尝试，而 `replan()` 直接走 `_validated_steps()`
    **不进这条 trace**——所以一轮里的 validation 全部来自 build 尝试，最终计划对应的是
    **最后一个被接受**的那次。取 `validations[0]` 会拿一份被丢弃的候选去比 gold，
    首偏离标签随之归错。
    """
    from support.intent_adversarial_judge import PlanSnapshot, StepSnapshot
    from support.intent_adversarial_trace import TraceSink, ValidationTrace

    def _snap(intent):
        return PlanSnapshot(
            steps=(StepSnapshot(id="s1", agent_id=intent.split(".")[0], intent=intent,
                                slots={}, depends_on=(), slot_refs={},
                                require_confirm=False),),
            complexity="simple", goal="", skills=(), exemplars=(),
            hint_effect="", catalog_stats={})

    turn = CaseTurn(utterance="附近的充电站", context={},
                    expected=TurnExpectation(plan=PlanExpectation(
                        assert_plan=True,
                        required_groups=(IntentGroup(("charging.find",)),),
                        allow_extra_intents=True)))
    good = ValidationTrace(raw_intents=("charging.find",),
                           raw_candidate=_snap("charging.find"),
                           admitted_intents=("charging.find",),
                           accepted=_snap("charging.find"), result="accepted")
    bad = ValidationTrace(raw_intents=("nearby.search",),
                          raw_candidate=_snap("nearby.search"),
                          admitted_intents=("charging.find",),
                          accepted=PlanSnapshot.empty(), result="rejected")

    def _pass(order):
        sink = TraceSink()
        sink.validations.extend(order)
        snapshot = SimpleNamespace(plan=_snap("charging.find"))
        outcome = cli._turn_outcome(
            turn, snapshot, SimpleNamespace(passed=True), sink, (0, 0, 0, 0))
        return outcome

    # 首错次对：证据必须来自**被接受**的那次，而不是被丢弃的第一次
    first_wrong = _pass((bad, good))
    assert first_wrong.raw_planner_pass is True
    # 幻觉率仍取全部尝试的并集——两个问题两份取法
    assert set(first_wrong.raw_intents) == {"charging.find", "nearby.search"}

    # 首错次也错（第二次被接受但候选就是错的）→ 如实报 False
    wrong_accepted = ValidationTrace(
        raw_intents=("nearby.search",), raw_candidate=_snap("nearby.search"),
        admitted_intents=("charging.find",), accepted=_snap("nearby.search"),
        result="accepted")
    assert _pass((bad, wrong_accepted)).raw_planner_pass is False

    # 两次都没被接受（随后落 `_fallback`）→ 没有 accepted 可绑，退回最后一次
    assert _pass((bad, bad)).raw_planner_pass is False

    # ⚠ 「首对次错」这个顺序在生产里**到不了**：`build()` 一旦接受就 break，
    # 不会再有第二次尝试。所以只需保证「取最后一个 accepted」，不必为不可达状态设计。
    from orchestrator.cloud import planning as _planning
    import inspect as _inspect
    build_src = _inspect.getsource(_planning.PlanBuilder.build)
    assert "break" in build_src, "build() 接受后必须 break，否则上面这条前提不成立"


def test_an_unlocked_or_drifted_l3_report_is_not_evidence(tmp_path):
    """反向构造：provider 串对得上，但报告自己说没锁住 / 中途漂移了。

    provider 串只说明「启动时想跑这个」；`locked=false` 或漂移意味着实际服务它的可能
    是别的模型。报告自己都声明作废了，不能当固定档位的证据。
    """
    def _write(name, lock):
        path = tmp_path / name / "journeys_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provider": "p:m", "run_id": "e2e-1", "provider_lock": lock,
            "journeys": [{"id": "A1-1", "status": "pass"}]}), encoding="utf-8")

    _write("unlocked", {"locked": False, "drift_detected": False})
    statuses, _, identity = read_l3_report(tmp_path, expect_provider="p:m")
    assert statuses == {} and any("l3_provider_not_locked" in r for r in identity)

    for path in tmp_path.glob("**/journeys_report.json"):
        path.unlink()
    _write("drifted", {"locked": True, "drift_detected": True, "drifts": ["a->b"]})
    statuses, _, identity = read_l3_report(tmp_path, expect_provider="p:m")
    assert statuses == {} and any("l3_provider_drift" in r for r in identity)


def test_two_run_ids_in_one_run_directory_is_not_one_run(tmp_path):
    """唯一目录里出现两个 runner run_id → 这批状态不来自同一次调用。

    我们注入不进 runner 自己生成的 run_id，但**可以要求同一个 run 目录里只有一个**。
    把两次运行的状态拼起来读，就是在编一份没发生过的运行。
    """
    for index, (name, journey) in enumerate((("a", "A1-1"), ("b", "A1-2"))):
        path = tmp_path / name / "journeys_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provider": "p:m", "run_id": f"e2e-{index}",
            "provider_lock": {"locked": True, "drift_detected": False},
            "journeys": [{"id": journey, "status": "pass"}]}), encoding="utf-8")

    _, _, identity = read_l3_report(tmp_path, expect_provider="p:m",
                                    expect_ids=["A1-1", "A1-2"])
    assert any("l3_run_id_mixed" in row for row in identity)


def test_a_finished_run_is_never_lost_to_a_console_encoding_error(tmp_path, monkeypatch):
    """反向构造：控制台编不出摘要里的某个字符。

    实测：一趟 470 单元的全量跑完、报告已落盘，`_print_summary` 打 `⚠`（U+26A0）时
    Windows GBK 控制台抛 `UnicodeEncodeError`，进程带 traceback 退出——**数据在，
    退出码却成了「运行失败」**。又一次「失败被记成了别的东西」，只是这次失败的是打印。
    """
    import io

    class _GbkOut(io.TextIOBase):
        def __init__(self):
            self.buf = []
            self.lossy = False

        def reconfigure(self, *, errors=None, **_kw):
            if errors == "replace":
                self.lossy = True

        def write(self, text):
            if not self.lossy:
                text.encode("gbk")          # 编不出就抛，与真实控制台一致
            self.buf.append(text)
            return len(text)

    out = _GbkOut()
    monkeypatch.setattr(sys, "stdout", out)
    cli._make_stdio_lossy()
    out.write("摘要里有一个 \u26a0 符号\n")     # 不再抛
    assert out.lossy and out.buf

    # 反向：没调用过降级时，同一句话确实会抛——证明上面那条不是恒真
    raw = _GbkOut()
    with pytest.raises(UnicodeEncodeError):
        raw.write("摘要里有一个 \u26a0 符号\n")


# ── 选集口径（2026-08-03，运行手册 §10 的 P3）─────────────────────────────
# 「`--tag composition` 会把 cp.adaptive.* 与 *.swapped 一起带进来，读子集报告前
# 得先看 --list」——修法是让报告自己说，不是改选择语义（那会破坏既有命令）。

from eval_intent_adversarial import (  # noqa: E402
    format_selection_provenance, selection_provenance,
)


def _prov(cases, argv, suite):
    return selection_provenance(cases, parse_args(argv), suite)


def test_selection_provenance_total_always_equals_the_real_selection():
    """**口径与实际选集必须同源。**

    这是这类功能最容易腐坏的地方：口径重算一遍过滤逻辑，两边慢慢走样，
    最后报告说「选了 A」而实际跑的是 B——而那种错没有任何红灯会发现。
    """
    cases = [_case("a", tags={"attacks": ["A4"], "mechanisms": ["composition"]}),
             _case("b", tags={"attacks": ["A1"], "mechanisms": ["parallel"]}),
             _case("c", status="stable", tags={"attacks": ["A4"]}, risk="high")]
    suite = _suite(("reviewed", "stable"), ("reviewed", "stable"))
    for argv in ([], ["--tag", "A4"], ["--risk", "high"], ["--case", "a"],
                 ["--cohort", "unseen_transfer"], ["--tag", "composition"]):
        assert (_prov(cases, argv, suite)["selected_total"]
                == len(select_cases(cases, parse_args(argv), suite))), argv


def test_full_run_prints_no_selection_lines():
    """无过滤器时一行都不打——口径行是给子集用的，全量跑批不该被它加噪声。"""
    cases = [_case("a"), _case("b")]
    prov = _prov(cases, [], _suite(("reviewed",), ("reviewed",)))
    assert prov["is_subset"] is False
    assert format_selection_provenance(prov) == []


def test_relation_bases_pulled_in_are_named_not_just_counted():
    """选中 variant 会自动带上 base（必须带，否则 relation 裁不了）——
    但那几条是怎么进来的，读的人此前无从知道。"""
    base = _case("base", family="fam", tags={"mechanisms": ["parallel"]})
    variant = _case("variant", family="fam", tags={"mechanisms": ["commute"]},
                    relation=RelationSpec("base", "clause_commute", {}))
    suite = _suite(("reviewed",), ("reviewed",))
    prov = _prov([base, variant], ["--tag", "commute"], suite)
    assert prov["matched_by_filters"] == 1
    assert prov["selected_total"] == 2
    assert prov["relation_bases_added"] == ["base"]
    rows = "\n".join(format_selection_provenance(prov))
    assert "这是子集" in rows and "base" in rows


def test_mechanism_mix_exposes_that_one_tag_spans_several_sub_families():
    """P3 抱怨的那件事本身：`composition` 看起来像「组合那一族」，
    实际 adaptive 与 commute 都在里面。分布摊开就一眼看得见。"""
    cases = [
        _case("p", tags={"mechanisms": ["composition", "parallel"]}),
        _case("a", tags={"mechanisms": ["composition", "adaptive"]}),
        _case("c", tags={"mechanisms": ["composition", "commute"]}),
    ]
    prov = _prov(cases, ["--tag", "composition"], _suite(("reviewed",), ("reviewed",)))
    assert prov["mechanism_mix"]["composition"] == 3
    assert prov["mechanism_mix"]["adaptive"] == 1
    rows = "\n".join(format_selection_provenance(prov))
    assert "机制分布" in rows and "adaptive" in rows and "commute" in rows


def test_a_tag_hitting_several_tag_keys_is_flagged():
    """同一个词同时命中 mechanisms 与 domains 时要出警告——
    选中的未必是你以为的那一族。"""
    cases = [_case("x", tags={"mechanisms": ["safety"], "domains": ["safety"]})]
    prov = _prov(cases, ["--tag", "safety"], _suite(("reviewed",), ("reviewed",)))
    assert prov["tag_hits"]["safety"]["matched_tag_keys"] == {"domains": 1,
                                                             "mechanisms": 1}
    assert "同时命中了多个 tag 键" in "\n".join(format_selection_provenance(prov))
