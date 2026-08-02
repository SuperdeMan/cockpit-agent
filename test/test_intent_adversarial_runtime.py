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
