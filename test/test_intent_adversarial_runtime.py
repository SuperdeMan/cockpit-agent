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
    RepeatOutcome, classify_repeats, run_catalog_l0, run_edge_turn,
    run_hint_turn, run_retrieval_turn, temporary_env,
)


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
    requested_ablations, skill_and_exemplar_inventory,
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


def test_ablations_only_run_for_failure_or_instability():
    assert requested_ablations("pass") == ()
    assert requested_ablations("stable_fail") == ABLATION_ARMS
    assert requested_ablations("unstable") == ABLATION_ARMS


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
