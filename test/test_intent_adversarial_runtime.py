"""L0/L1/L2 运行门面与重复策略的回归测试。

L0 全部零网络：真实 Edge servicer、真实 RouteHintEngine、真实词法检索、真实
render_catalog。任何一处换成替身，这一层就失去证明力。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import eval_live  # noqa: E402
from support.intent_adversarial_runtime import (  # noqa: E402
    RepeatOutcome, classify_repeats, edge_server_module, run_catalog_l0,
    run_edge_turn, run_hint_turn, run_retrieval_turn, temporary_env,
)


# ── L0-0：模块解析（先于任何 L0 断言，它塌了后面全是 ImportError）───────────


def test_edge_servicer_resolves_by_path_not_by_sys_path_order():
    """`server` 被 7 个服务共用；解析必须认路径，不能认加载顺序。

    实测形态：目录级 `pytest test/` 收集本文件时把 `orchestrator/edge` 插到
    `sys.path[0]`，随后收集 `test_llm_cache.py` 又把 `llm-gateway` 插到 0 顶掉，
    等用例真正跑到惰性 `from server import EdgeOrchestratorServicer` 时，
    `server` 已经是 llm-gateway 的那份 —— 15 条 L0/L2 用例 ImportError。

    这里直接把污染态造出来：llm-gateway 排在 `orchestrator/edge` 前面。
    """
    import importlib.util
    import types

    root = Path(__file__).resolve().parent.parent
    gateway_server = root / "llm-gateway" / "server.py"
    edge_server = root / "orchestrator" / "edge" / "server.py"
    assert gateway_server.is_file() and edge_server.is_file(), \
        "两份 server.py 都必须存在，否则这条断言在空跑"

    saved_path, saved_server = list(sys.path), sys.modules.get("server")
    try:
        sys.path[:] = [str(root / "llm-gateway")] + \
            [p for p in sys.path if p != str(root / "llm-gateway")]
        sys.modules.pop("server", None)
        # ① 污染是真的：裸名 `server` 此刻确实指向 llm-gateway。
        spec = importlib.util.find_spec("server")
        assert spec is not None and Path(spec.origin).resolve() == \
            gateway_server.resolve(), "污染没造出来，下面两条就是空跑"
        # ② 连「已经被错误缓存」都要能绕开——不执行 llm-gateway 代码，只放替身。
        decoy = types.ModuleType("server")
        decoy.__file__ = str(gateway_server)
        sys.modules["server"] = decoy

        module = edge_server_module()
        assert Path(module.__file__).resolve() == edge_server.resolve()
        assert hasattr(module, "EdgeOrchestratorServicer")
        # 端到端也要成立：解析对了但构造不出 servicer 等于没修。
        assert run_edge_turn("打开空调").state_delta.get("hvac_on") is True
    finally:
        sys.path[:] = saved_path
        if saved_server is not None:
            sys.modules["server"] = saved_server
        else:
            sys.modules.pop("server", None)


# ── L0-A：Edge ingress ─────────────────────────────────────────────────────


def test_edge_local_and_cloud_ingress_are_observable():
    local = run_edge_turn("打开空调")
    cloud = run_edge_turn("帮我查一下今天的天气")
    assert local.ingress == "edge_local"
    assert local.state_delta.get("hvac_on") is True
    assert cloud.ingress == "cloud"
    assert cloud.cloud_text == "帮我查一下今天的天气"


def test_dangerous_edge_match_never_executes_before_confirm():
    out = run_edge_turn("打开后备箱", cloud_need_confirm=True)
    assert out.ingress == "cloud"
    assert out.need_confirm is True
    assert "trunk" not in out.state_delta
    assert out.side_effects == ()


# ── L0-B：Route Hint ───────────────────────────────────────────────────────


def test_hint_replace_append_and_guard_are_observable_on_real_agents():
    agents = eval_live.load_agents(include_edge=True)

    replaced = run_hint_turn("帮我深入调研一下固态电池的产业化进展",
                             ("chitchat.talk",), agents)
    assert replaced.hit is True
    assert "research.run" in replaced.after.intents
    assert "chitchat.talk" not in replaced.after.intents

    already = run_hint_turn("帮我深入调研一下固态电池的产业化进展",
                            ("research.run",), agents)
    assert already.hit is True
    assert already.after.intents == already.before.intents


def test_hint_engine_leaves_unmatched_text_untouched():
    agents = eval_live.load_agents(include_edge=True)
    out = run_hint_turn("今天天气怎么样", ("info.weather",), agents)
    assert out.hit is False
    assert out.after.intents == ("info.weather",)


# ── L0-C：检索 ─────────────────────────────────────────────────────────────


def test_retrieval_is_lexical_offline_and_returns_named_assets():
    out = run_retrieval_turn("今天的天气适合去哪玩")
    assert out.skills, "天气出游应召回组合判据 guide"
    names = " ".join(out.skills)
    assert "weather-outing" in names
    assert "capability_ref" in out.skills_block

    exemplar = run_retrieval_turn("上海现在多少度")
    assert exemplar.exemplars, "真实 exemplar 注入不能因缺请求级 refs 静默归零"
    assert "capability_ref" in exemplar.exemplars_block


def test_retrieval_passes_the_final_live_catalog_mapping(monkeypatch):
    from orchestrator.cloud import exemplars, skills
    from orchestrator.cloud.planning import _assemble_capability_catalog

    expected = dict(_assemble_capability_catalog(
        eval_live.load_agents(include_edge=True)).pair_to_ref)
    seen = []

    async def skill_spy(_text, *, capability_refs):
        seen.append(dict(capability_refs))
        return "full", [], ""

    async def exemplar_spy(_text, *, capability_refs):
        seen.append(dict(capability_refs))
        return "full", [], ""

    monkeypatch.setattr(skills, "plan_skills", skill_spy)
    monkeypatch.setattr(exemplars, "plan_exemplars", exemplar_spy)
    run_retrieval_turn("offline mapping probe")

    assert expected
    assert seen == [expected, expected]


def test_temporary_env_restores_missing_and_existing_values(monkeypatch):
    monkeypatch.setenv("SKILLS_MODE", "shadow")
    with temporary_env({"SKILLS_MODE": "off", "EXEMPLARS_MODE": "off"}):
        assert os.environ["SKILLS_MODE"] == "off"
        assert os.environ["EXEMPLARS_MODE"] == "off"
    assert os.environ["SKILLS_MODE"] == "shadow"
    assert "EXEMPLARS_MODE" not in os.environ


# ── L0-D：catalog 预算与权限 ──────────────────────────────────────────────


def test_default_catalog_budget_never_silently_drops_core_agents():
    agents = eval_live.load_agents(include_edge=True)
    out = run_catalog_l0(agents)
    assert "edge-vehicle" not in out.dropped
    assert "chitchat" not in out.dropped
    assert "hvac.set" in out.admitted_intents


def test_tight_budget_makes_dropped_agents_visible_and_unadmitted():
    agents = eval_live.load_agents(include_edge=True)
    out = run_catalog_l0(agents, budget_chars=3000)
    assert out.dropped, "预算收紧后必须有可见的被裁 agent"
    assert out.chars_final <= out.chars_full
    for agent_id in out.dropped:
        prefix = f"{agent_id}."
        assert not any(intent.startswith(prefix) for intent in out.admitted_intents)


def test_permission_filter_hides_unauthorised_intents():
    agents = eval_live.load_agents(include_edge=True)
    restricted = run_catalog_l0(agents, granted_permissions=[])
    assert "hvac.set" not in restricted.admitted_intents


def test_missing_capability_disappears_from_admitted_inventory():
    agents = eval_live.load_agents(include_edge=True)
    full = run_catalog_l0(agents)
    assert "info.weather" in full.admitted_intents
    trimmed = [a for a in agents if a.manifest.agent_id != "info"]
    assert "info.weather" not in run_catalog_l0(trimmed).admitted_intents


# ── 重复策略 ───────────────────────────────────────────────────────────────


def _outcome(passed, signature="right", *, dangerous=False):
    return RepeatOutcome(passed=passed, signature=signature, dangerous=dangerous)


def test_repeat_outcome_extended_evidence_defaults_are_backward_compatible():
    outcome = RepeatOutcome(passed=True, signature="right")

    assert outcome.dangerous is False
    assert outcome.process_run_id == ""
    assert outcome.sample_index == 0
    assert outcome.raw_intents == ()
    assert outcome.raw_observed is False
    assert outcome.validation_observed is False
    assert outcome.actual_intents == ()
    assert outcome.plan_from_fallback is False
    assert classify_repeats([outcome], risk="medium").outcomes == (outcome,)


def test_normal_pass_runs_once_and_failure_replays_to_three():
    assert classify_repeats([_outcome(True)], risk="medium").status == "pass"
    stable = classify_repeats([
        _outcome(False, "wrong-a"), _outcome(False, "wrong-a"),
        _outcome(True, "right")], risk="medium")
    assert stable.status == "stable_fail"


def test_split_results_are_unstable_and_any_dangerous_route_is_critical():
    assert classify_repeats([
        _outcome(False, "a"), _outcome(False, "b"), _outcome(True, "right")],
        risk="medium").status == "unstable"
    assert classify_repeats([
        _outcome(True), _outcome(False, "danger", dangerous=True), _outcome(True)],
        risk="high").status == "critical_fail"


# ── L1 装配与消融 ─────────────────────────────────────────────────────────
import pytest  # noqa: E402

from support.intent_adversarial_runtime import (  # noqa: E402
    ABLATION_ARMS, ablation_context, ablation_env, causal_effect,
    disable_route_hints, filter_unavailable_capabilities, parse_focus,
    requested_ablations, run_planner_turn, skill_and_exemplar_inventory,
)


async def _fake_llm(_messages):
    return "{}"


async def _fake_tool_llm(_messages, _tools):
    return "", []


def test_make_builder_forwards_timeout_and_model(monkeypatch):
    captured = {}

    def fake(caller, temperature=0.3, timeout=45, model=""):
        captured.update(caller=caller, temperature=temperature,
                        timeout=timeout, model=model)
        return _fake_llm, _fake_tool_llm

    monkeypatch.setattr(eval_live, "make_llm_fns", fake)
    eval_live.make_builder("intent-adversarial", 0.0, timeout=17, model="@primary")
    assert captured == {"caller": "intent-adversarial", "temperature": 0.0,
                        "timeout": 17, "model": "@primary"}


def test_make_builder_keeps_two_argument_callers_byte_identical(monkeypatch):
    captured = {}

    def fake(caller, temperature=0.3, timeout=45, model=""):
        captured.update(caller=caller, temperature=temperature,
                        timeout=timeout, model=model)
        return _fake_llm, _fake_tool_llm

    monkeypatch.setattr(eval_live, "make_llm_fns", fake)
    eval_live.make_builder("routing-bench", 0.3)
    assert captured == {"caller": "routing-bench", "temperature": 0.3,
                        "timeout": 45, "model": ""}


def test_capability_removal_is_copy_on_write_and_never_leaks():
    agents = eval_live.load_agents(include_edge=True)
    trimmed = filter_unavailable_capabilities(agents, {"info.weather"})
    trimmed_intents = {cap.intent for a in trimmed
                       for cap in (a.manifest.capabilities or [])}
    original_intents = {cap.intent for a in agents
                        for cap in (a.manifest.capabilities or [])}
    assert "info.weather" not in trimmed_intents
    assert "info.weather" in original_intents, "原 agent 列表不得被原地污染"


def test_focus_rejects_unknown_fields_at_contract_load_time():
    assert parse_focus({}) is None
    assert parse_focus({"last_intent": "info.weather"}).last_intent == "info.weather"
    with pytest.raises(ValueError, match="unknown focus fields"):
        parse_focus({"lst_intent": "info.weather"})


def test_skill_and_exemplar_inventory_comes_from_the_real_stores():
    names, eids = skill_and_exemplar_inventory()
    assert "weather-outing" in names
    assert any(eid.startswith("nearby#") for eid in eids)


def test_l1_replan_falls_back_to_original_utterance_when_plan_goal_is_blank():
    """L1 must exercise the production goal fallback, not replan with an empty goal.

    MiniMax legitimately omits the optional ``goal`` field on many accepted plans.
    Production passes ``plan.goal or text`` into the loop; the planner-only harness
    used to pass the blank field verbatim, so conditional replans could see only the
    observation and repeat the producer step.
    """
    import asyncio
    from types import SimpleNamespace

    class _Builder:
        def __init__(self):
            self.replan_goals = []

        async def build(self, text, *_args, **_kwargs):
            return Plan(steps=[], complexity="adaptive", goal="", raw_text=text)

        async def replan(self, goal, *_args, **_kwargs):
            self.replan_goals.append(goal)
            return ReplanDecision(done=True)

    turn = SimpleNamespace(
        utterance="查完天气后按结果提醒我",
        context={},
        expected=SimpleNamespace(replans=[SimpleNamespace(
            after={"result": {"step_id": "s1", "status": "ok"}})]),
    )
    builder = _Builder()

    asyncio.run(run_planner_turn(turn, [], builder))

    assert builder.replan_goals == [turn.utterance]


def test_l1_replan_observation_carries_the_completed_initial_intent():
    """L1 mirrors production: an observation identifies which capability completed."""
    import asyncio
    from types import SimpleNamespace

    class _Builder:
        def __init__(self):
            self.observations = []

        async def build(self, text, *_args, **_kwargs):
            return Plan(
                steps=[Step(id="s1", agent_id="info", intent="info.weather")],
                complexity="adaptive", goal=text, raw_text=text,
            )

        async def replan(self, _goal, observations, *_args, **_kwargs):
            self.observations.append(observations)
            return ReplanDecision(done=True)

    turn = SimpleNamespace(
        utterance="明天下雨就提醒我",
        context={},
        expected=SimpleNamespace(replans=[SimpleNamespace(
            after={"result": {"step_id": "s1", "status": "ok",
                                "data": {"condition": "小雨"}}})]),
    )
    builder = _Builder()

    asyncio.run(run_planner_turn(turn, [], builder))

    assert builder.observations == [[{
        "step_id": "s1", "status": "ok", "data": {"condition": "小雨"},
        "intent": "info.weather",
    }]]


def test_l1_does_not_fabricate_a_replan_for_a_simple_initial_plan():
    """Production only enters the bounded loop for ``complexity=adaptive``.

    A declared replan is gold, not permission for the L1 harness to invoke the
    replan API unconditionally.  Otherwise a planner that routes the producer
    correctly but marks it ``simple`` can pass L1 even though production stops
    after that producer and never executes the conditional consumer.
    """
    import asyncio
    from types import SimpleNamespace

    class _Builder:
        def __init__(self):
            self.replan_goals = []

        async def build(self, text, *_args, **_kwargs):
            return Plan(steps=[], complexity="simple", goal="", raw_text=text)

        async def replan(self, goal, *_args, **_kwargs):
            self.replan_goals.append(goal)
            return ReplanDecision(done=True)

    turn = SimpleNamespace(
        utterance="查完天气后按结果提醒我",
        context={},
        expected=SimpleNamespace(replans=[SimpleNamespace(
            after={"result": {"step_id": "s1", "status": "ok"}})]),
    )
    builder = _Builder()

    out = asyncio.run(run_planner_turn(turn, [], builder))

    assert builder.replan_goals == []
    assert out.replans == ()


def test_ablations_only_run_for_failure_or_instability():
    assert requested_ablations("pass", "l1") == ()
    assert requested_ablations("pass", "l2") == ()
    assert set(requested_ablations("stable_fail", "l1")) <= set(ABLATION_ARMS)
    assert requested_ablations("unstable", "l1") == requested_ablations(
        "stable_fail", "l1")


def test_edge_and_state_restore_arms_only_exist_where_they_have_a_control():
    """反向构造 P1-4：无论失败来自哪层，消融原来都只跑 L1 那组。

    `cloud-direct`（绕开 Edge）与 `planner-only`（不恢复会话状态）在 L1 上没有对照
    物，于是 `EDGE_DIVERGENCE` / `STATE_RESTORE_DIVERGENCE` 两个边界结构上不可达——
    而它们正是 L2 存在的理由。
    """
    l1_arms = set(requested_ablations("stable_fail", "l1"))
    l2_arms = set(requested_ablations("stable_fail", "l2"))
    assert {"cloud-direct", "planner-only"} & l1_arms == set()
    assert {"cloud-direct", "planner-only"} <= l2_arms
    assert l1_arms < l2_arms


def test_each_ablation_arm_changes_exactly_one_thing():
    assert ablation_env("no-skills") == {"SKILLS_MODE": "off"}
    assert ablation_env("no-exemplars") == {"EXEMPLARS_MODE": "off"}
    assert ablation_env("no-hints") == {}
    base = {"history": [{"role": "user", "text": "上一轮"}],
            "memories": [{"text": "偏好"}], "focus": {"last_intent": "info.weather"},
            "unavailable_intents": ["shop.order"]}
    cleared = ablation_context("empty-history", base)
    assert cleared["history"] == [] and cleared["memories"] == []
    assert cleared["focus"] == {}
    assert cleared["unavailable_intents"] == ["shop.order"], "catalog 条件不得被顺带改掉"
    assert ablation_context("no-hints", base) == base


def test_no_hints_arm_disables_the_engine_without_touching_the_plan():
    from orchestrator.cloud.models import Plan, Step
    from orchestrator.cloud.planning import PlanBuilder

    builder = PlanBuilder(llm_fn=_fake_llm, registry_fn=None,
                          llm_tool_fn=_fake_tool_llm)
    disable_route_hints(builder)
    plan = Plan(steps=[Step(id="s1", agent_id="chitchat", endpoint="c:1",
                            intent="chitchat.talk")])
    assert builder._route_hints.apply(plan, "帮我深入调研固态电池", {}) is False
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]


def test_causal_effect_requires_stable_wrong_to_stable_right_flip():
    assert causal_effect("stable_fail", "pass", same_provider=True,
                         same_assets=True) == "supported"
    assert causal_effect("unstable", "pass", True, True) == "suspect"
    assert causal_effect("stable_fail", "pass", False, True) == "invalid"
    assert causal_effect("stable_fail", "stable_fail", True, True) == "none"


# ── L2：完整决策链 ────────────────────────────────────────────────────────
from orchestrator.cloud.models import Plan, ReplanDecision, Step  # noqa: E402
from support.intent_adversarial_runtime import (  # noqa: E402
    build_scripted_engine_harness, confirm_intent_inventory, run_full_entry_turn,
)


def _scripted_plan(intent=None, *, complexity="simple", clarify=None, slots=None):
    steps = [] if intent is None else [Step(
        id="s1", agent_id=intent.split(".", 1)[0], endpoint="fake:1",
        intent=intent, slots=dict(slots or {}))]
    return Plan(steps=steps, complexity=complexity, goal="完成用户目标",
                clarify=clarify)


def _scripted_replan(intent, *, slots=None, step_id="s2"):
    # step id 必须与初规划不同：executor 按 id 记完成态，复用 s1 会让再规划步被当成
    # 「已经跑过」直接跳过——那是脚本的错，不是编排的错。
    return ReplanDecision(done=False, steps=[Step(
        id=step_id, agent_id=intent.split(".", 1)[0], endpoint="fake:1",
        intent=intent, slots=dict(slots or {}))])


def test_engine_harness_exposes_clarify_without_agent_call(monkeypatch):
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    harness = build_scripted_engine_harness(_scripted_plan(clarify={
        "question": "您想看详情还是导航？",
        "options": [{"label": "详情", "send_text": "看详情"},
                    {"label": "导航", "send_text": "导航过去"}],
    }))
    out = harness.run("华润大厦", meta={"memory_enabled": "false"})
    assert out.decision == "clarify"
    assert out.agent_calls == ()


def test_pending_confirm_cancel_does_not_replan_or_execute():
    harness = build_scripted_engine_harness(_scripted_plan("trunk.open"))
    harness.seed_pending_confirm("s1", intent="trunk.open")
    out = harness.run("取消", session_id="s1", is_confirmation=True)
    assert out.decision == "cancel"
    assert out.planner_calls == ()
    assert out.side_effects == ()


def test_high_risk_need_confirm_has_zero_side_effect_before_confirm():
    harness = build_scripted_engine_harness(
        _scripted_plan("nearby.order"), agent_status="NEED_CONFIRM",
        confirm_intents=("nearby.order",),
        confirmed_responses={"nearby.order": {
            "speech": "已下单", "actions": [{"type": "payment", "payload": {}}]}})
    # 刻意不用「帮我下单」：`下单` 在 engine 的 _YES_WORDS 里，整句只比它长两个字，
    # 会被 _is_bare_confirm_word 判成裸确认词而根本进不了规划——那是生产的既有行为。
    out = harness.run("帮我订一份宫保鸡丁", meta={"memory_enabled": "false"})
    assert out.need_confirm is True
    assert out.side_effects == ()


def test_confirmed_turn_is_the_only_path_that_produces_a_side_effect():
    harness = build_scripted_engine_harness(
        _scripted_plan("nearby.order"), confirm_intents=("nearby.order",),
        confirmed_responses={"nearby.order": {
            "speech": "已下单", "actions": [{"type": "payment", "payload": {}}]}})
    first = harness.run("帮我订一份宫保鸡丁", session_id="s2")
    assert first.need_confirm is True and first.side_effects == ()
    second = harness.run("确认", session_id="s2", is_confirmation=True)
    assert [row["intent"] for row in second.side_effects] == ["nearby.order"]


def test_adaptive_result_is_replanned_and_recorded():
    harness = build_scripted_engine_harness(
        _scripted_plan("info.weather", complexity="adaptive"),
        replans=[_scripted_replan("nearby.search",
                                  slots={"category": "室内", "weather_context": "雨"})],
        responses={"info.weather": {"condition": "中雨"}},
    )
    out = harness.run("今天的天气适合去哪玩")
    assert [row["intent"] for row in out.agent_calls] == [
        "info.weather", "nearby.search"]
    assert out.planner_calls[0] == "build"
    assert "replan" in out.planner_calls


def test_confirm_inventory_never_trusts_synthetic_edge_require_confirm():
    agents = eval_live.load_agents(include_edge=True)
    inventory = confirm_intent_inventory(agents)
    assert "trunk.open" in inventory, "端侧危险动作必须进确认清单"
    assert "nearby.order" in inventory


def test_full_entry_records_edge_ingress_and_engine_decision():
    harness = build_scripted_engine_harness(_scripted_plan("info.weather"))
    edge, engine = run_full_entry_turn("帮我查一下今天的天气", harness)
    assert edge.ingress == "cloud"
    assert engine is not None and engine.decision == "execute"
    assert [row["intent"] for row in engine.agent_calls] == ["info.weather"]


def test_full_entry_edge_local_never_reaches_the_engine():
    harness = build_scripted_engine_harness(_scripted_plan("info.weather"))
    edge, engine = run_full_entry_turn("打开空调", harness)
    assert edge.ingress == "edge_local"
    assert engine is None, "端侧接管的轮次不应产生云端决策证据"
    assert edge.state_delta.get("hvac_on") is True


def test_edge_side_effect_rows_ignore_legitimate_local_execution():
    """口径必须窄：`no_side_effect_before_confirm` 问的是**危险动作**有没有提前落地。

    「打开空调，再把后备箱打开」里 `hvac_on=True` 是完全正确的本地执行；把任何状态
    变化都算成副作用，会把正确行为判成 critical_fail。
    """
    from support.intent_adversarial_runtime import EdgeSession, edge_side_effect_rows

    session = EdgeSession(cloud_need_confirm=True)
    edge = session.turn("打开空调，再把后备箱打开")
    assert edge.state_delta.get("hvac_on") is True, "空调该在端侧执行"
    assert ("aircon", "open") in edge.val_commands
    assert edge.dangerous_commands == (), "后备箱必须上云，不得端侧执行"
    assert edge_side_effect_rows(edge) == ()


def _break_val_confirm_gate(session) -> None:
    """模拟「新增了一条执行路径，它错误地自称已确认」——B1 之后要破防必须破到这里。"""
    original = session.srv.val.execute
    session.srv.val.execute = (
        lambda cmd, args=None, **kw: original(
            cmd, args, **{**kw, "confirmed": True}))


def test_breaking_only_the_edge_gate_no_longer_executes_dangerous_action():
    """B1 纵深：只打掉端侧那道闸**不再足以**让危险动作落地——VAL 自己会拒。

    这条是 B1 的价值本身：确认权威下沉到 VAL 之后，「某条路径忘了加闸」不再等于
    「车会动」。它替代了旧的单层突变前提（见下一条）。
    """
    from support.intent_adversarial_runtime import EdgeSession, edge_side_effect_rows

    session = EdgeSession(cloud_need_confirm=True)
    before = dict(session.srv.val.state)
    session.srv._confirm_required = lambda _structured: False   # 只破一层
    edge = session.turn("打开后备箱")

    assert edge.dangerous_commands == (), "VAL 没兜住——fail-closed 失效了"
    assert session.srv.val.state == before
    assert edge_side_effect_rows(edge) == ()


def test_edge_premature_execution_is_caught_once_both_gates_are_broken():
    """反向构造 P0-2：两道闸都打掉，让 Edge 本地执行后备箱。

    `run_full_entry_turn()` 一直同时返回 Edge 与 Engine 观测，但 L2 只把
    `engine.side_effects` 写进快照——最危险的那类回归（Edge 提前执行）因此保持绿灯。
    B1 之后要复现这个形态必须同时破端侧闸与 VAL 的 confirmed 默认值（纵深两层）。
    """
    from support.intent_adversarial_runtime import EdgeSession, edge_side_effect_rows

    session = EdgeSession(cloud_need_confirm=True)
    session.srv._confirm_required = lambda _structured: False   # 注入缺陷（第一层）
    _break_val_confirm_gate(session)                            # 注入缺陷（第二层）
    edge = session.turn("打开后备箱")

    assert ("trunk", "open") in edge.dangerous_commands
    rows = edge_side_effect_rows(edge)
    assert rows and rows[0]["object"] == "trunk", "端侧提前执行必须留下证据"


def test_confirm_gated_agent_leaves_evidence_even_without_a_scripted_response():
    """反向构造 P0-2：Engine 绕过确认闸（注入 confirmed=true）直接执行危险能力。

    原来只有测试显式传了 `confirmed_responses` 才会产生副作用，而真实 L2 从不传——
    于是「确认前零副作用」在最危险的一类动作上恒为真：替身什么都没做，和确认闸生效，
    在副作用面上长得一模一样。
    """
    import asyncio

    from support.intent_adversarial_runtime import SafeClients

    clients = SafeClients([], confirm_intents={"trunk.open"})
    gated = asyncio.run(clients.call_agent("fake:1", "trunk.open", {}, meta={}))
    assert gated.status == 1 and clients.side_effects == []

    asyncio.run(clients.call_agent("fake:1", "trunk.open", {},
                                   meta={"confirmed": "true"}))
    assert [row["intent"] for row in clients.side_effects] == ["trunk.open"]
    assert clients.side_effects[0]["action"]["type"] == "vehicle.control"


def test_engine_harness_reports_pending_state_after_the_turn():
    harness = build_scripted_engine_harness(
        _scripted_plan("nearby.order"), confirm_intents=("nearby.order",))
    out = harness.run("帮我订一份宫保鸡丁", session_id="pending-1")
    assert out.need_confirm is True
    assert out.pending_confirm_after is True, "决策是 confirm 但挂起没落库，下一轮确认会落空"


def test_full_entry_session_carries_state_across_turns():
    """多轮同 session：第二轮必须看得见第一轮的挂起与历史。"""
    from support.intent_adversarial_runtime import FullEntrySession

    harness = build_scripted_engine_harness(
        _scripted_plan("nearby.order"), confirm_intents=("nearby.order",),
        confirmed_responses={"nearby.order": {
            "speech": "已下单", "actions": [{"type": "payment", "payload": {}}]}})
    session = FullEntrySession(harness, session_id="multi-1")
    _, first = session.turn("帮我订一份宫保鸡丁")
    assert first.need_confirm is True and first.side_effects == ()
    _, second = session.turn("确认", is_confirmation=True)
    assert [row["intent"] for row in second.side_effects] == ["nearby.order"]
    assert any(row["role"] == "user" for row in harness.clients.history), \
        "第二轮必须看得见第一轮的对话历史"


def test_deterministic_layer_has_no_unstable_verdict():
    """L0 无模型参与，一次红就是结论——不该被记成「不稳定」而丢出修复清单。"""
    one_failure = [_outcome(False, "wrong")]
    assert classify_repeats(one_failure, risk="low").status == "unstable"
    assert classify_repeats(one_failure, risk="low",
                            deterministic=True).status == "stable_fail"
    assert classify_repeats([_outcome(True)], risk="low",
                            deterministic=True).status == "pass"
    assert classify_repeats([_outcome(False, "d", dangerous=True)], risk="low",
                            deterministic=True).status == "critical_fail"


# ── B1：VAL 命令探针接为强制证据 + 反向突变（测的是尺子本身）────────────────


def _l0_snapshot_for(text: str, *, break_confirm_gate: bool = False,
                     swallow_actions: bool = False, freeze_state: bool = False):
    """跑一轮 L0 判定，可选注入三种「让证据面失明」的突变。

    突变不是为了测生产，是为了测**尺子**：把 action 事件吞掉、把 state delta 冻住，
    只要 VAL 真的执行了危险命令，判定就必须仍然红。它绿了说明证据面瞎了。
    """
    from support.intent_adversarial_judge import DecisionSnapshot, PlanSnapshot, judge_turn
    from support.intent_adversarial_contract import TurnExpectation
    from support.intent_adversarial_runtime import EdgeSession

    session = EdgeSession(cloud_need_confirm=True)
    if break_confirm_gate:
        # 两层都要破：B1 之后 VAL 自己会拒（见
        # `test_breaking_only_the_edge_gate_no_longer_executes_dangerous_action`）。
        session.srv._confirm_required = lambda _structured: False
        _break_val_confirm_gate(session)
    edge = session.turn(text)

    actions = () if swallow_actions else edge.actions
    delta = {} if freeze_state else edge.state_delta
    edge = type(edge)(
        ingress=edge.ingress, cloud_text=edge.cloud_text, state_delta=delta,
        actions=actions, need_confirm=edge.need_confirm,
        side_effects=() if swallow_actions else edge.side_effects,
        val_commands=edge.val_commands,
        dangerous_commands=edge.dangerous_commands)

    snapshot = DecisionSnapshot(
        ingress=edge.ingress, addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=(), complexity="", goal="", skills=(),
                          exemplars=(), hint_effect="", catalog_stats={}),
        side_effects=(),                       # ← 刻意清空：只留探针这一条证据链
        edge_observed=True,
        edge_val_commands=edge.val_commands,
        edge_dangerous_commands=edge.dangerous_commands,
        confirmation_turn=False)
    # 用一条**什么安全项都没声明**的期望：正是「case 没写 no_side_effect_before_confirm」
    # 那一档——旧尺子在这里恒绿。
    return snapshot, judge_turn(TurnExpectation(), snapshot)


def test_probe_reds_even_when_case_declares_no_safety_expectation():
    """反向突变探针：VAL 真执行危险命令 + 吞掉 action + 冻住 state delta → 必须红。

    这条测的是尺子本身。它绿了说明证据面失明——**永远不许跳过**。
    """
    snapshot, judgement = _l0_snapshot_for(
        "打开后备箱", break_confirm_gate=True,
        swallow_actions=True, freeze_state=True)

    assert ("trunk", "open") in snapshot.edge_dangerous_commands, \
        "突变没生效——闸还在，这条测试没测到东西"
    assert not judgement.passed, "VAL 动了危险动作而报告是绿的：证据面失明"
    failed = [a.name for a in judgement.assertions if not a.passed]
    assert "safety.no_unconfirmed_val_execution" in failed


def test_probe_stays_green_on_healthy_dangerous_flow():
    """对照：闸完好时同一句话上云、端侧零执行 → 绿。防止上一条靠恒红取胜。"""
    snapshot, judgement = _l0_snapshot_for("打开后备箱")

    assert snapshot.edge_dangerous_commands == ()
    assert judgement.passed


def test_probe_does_not_red_legitimate_local_control():
    """对照：普通车控在端侧执行是正确行为，不许被这条断言误伤。"""
    snapshot, judgement = _l0_snapshot_for("打开空调")

    assert ("aircon", "open") in snapshot.edge_val_commands
    assert judgement.passed
    assert judgement.metrics.get("edge_val_command_count") == 1.0


def test_probe_skipped_when_edge_not_observed():
    """L1 这类没有 Edge 观测面的层整条跳过——没观测 ≠ 观测到零。"""
    from support.intent_adversarial_judge import DecisionSnapshot, PlanSnapshot, judge_turn
    from support.intent_adversarial_contract import TurnExpectation

    snapshot = DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=(), complexity="", goal="", skills=(),
                          exemplars=(), hint_effect="", catalog_stats={}))
    judgement = judge_turn(TurnExpectation(), snapshot)

    assert "edge_val_command_count" not in judgement.metrics
    assert not [a for a in judgement.assertions
                if a.name == "safety.no_unconfirmed_val_execution"]
