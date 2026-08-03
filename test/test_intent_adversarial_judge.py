"""精确计划集合与最小对照关系裁判的回归测试（合成快照，不跑 Planner）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from support.intent_adversarial_contract import (  # noqa: E402
    DependencyExpectation, IntentGroup, PlanExpectation, ReplanExpectation,
    SlotExpectation, TurnExpectation,
)
from support.intent_adversarial_judge import (  # noqa: E402
    DecisionSnapshot, PlanSnapshot, StepSnapshot, judge_turn,
)


def _step(id_, intent, *, depends_on=(), slots=None, slot_refs=None):
    return StepSnapshot(id=id_, agent_id=intent.split(".")[0], intent=intent,
                        slots=slots or {}, depends_on=tuple(depends_on),
                        slot_refs=slot_refs or {}, require_confirm=False)


def _snapshot(*intents):
    return DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=tuple(_step(f"s{i}", intent)
                                      for i, intent in enumerate(intents, 1)),
                          complexity="simple", goal="", skills=(), exemplars=(),
                          hint_effect="", catalog_stats={}),
    )


def test_required_groups_are_and_and_group_members_are_or():
    expected = TurnExpectation(plan=PlanExpectation(assert_plan=True, required_groups=(
        IntentGroup(("info.weather", "info.forecast")),
        IntentGroup(("nearby.search",)),
    )))
    judgement = judge_turn(expected, _snapshot("info.forecast"))
    assert not judgement.passed
    assert judgement.metric("required_group_recall") == 0.5


def test_forbidden_intent_fails_even_when_required_is_present():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("charging.find",)),),
        forbidden_intents=("nearby.search",),
    ))
    judgement = judge_turn(expected, _snapshot("charging.find", "nearby.search"))
    assert not judgement.passed
    assert judgement.metric("forbidden_route_count") == 1


def test_unapproved_extra_intent_fails():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("info.weather",)),),
        allow_extra_intents=False,
    ))
    judgement = judge_turn(expected, _snapshot("info.weather", "info.news"))
    assert not judgement.passed
    assert judgement.metric("overroute_count") == 1


def test_dependency_and_carried_slot_are_both_required():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("nearby.search",)), IntentGroup(("nearby.order",))),
        dependencies=(DependencyExpectation(("nearby.search",), "nearby.order",
                                            ("name",)),),
    ))
    plan = PlanSnapshot(
        steps=(
            _step("s1", "nearby.search"),
            _step("s2", "nearby.order", depends_on=("s1",),
                  slot_refs={"name": "s1.data.items.0.name"}),
        ), complexity="simple", goal="", skills=(), exemplars=(),
        hint_effect="", catalog_stats={})
    result = judge_turn(expected, DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False, plan=plan))
    assert result.passed
    assert result.metric("dependency_pass") == 1.0


def test_one_valid_duplicate_cannot_hide_an_unlinked_consumer():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("nearby.search",)),
                         IntentGroup(("nearby.order",))),
        dependencies=(DependencyExpectation(
            ("nearby.search",), "nearby.order", ("name",)),),
    ))
    plan = PlanSnapshot(
        steps=(
            _step("s1", "nearby.search"),
            _step("s2", "nearby.order", depends_on=("s1",),
                  slot_refs={"name": "s1.data.items.0.name"}),
            _step("s3", "nearby.order"),
        ), complexity="simple", goal="", skills=(), exemplars=(),
        hint_effect="", catalog_stats={})
    actual = DecisionSnapshot("cloud", True, "execute", False, plan)
    assert not judge_turn(expected, actual).passed


def test_slot_matchers_cover_exact_one_of_range_presence_and_source_reference():
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True,
        required_groups=(IntentGroup(("nearby.search",)),),
        slots=(
            SlotExpectation("nearby.search", "category", "exact", value="室内"),
            SlotExpectation("nearby.search", "sort", "one_of", allowed=("rating", "distance")),
            SlotExpectation("nearby.search", "radius", "range", minimum=500, maximum=5000),
            SlotExpectation("nearby.search", "weather_context", "presence"),
            SlotExpectation("nearby.search", "location", "source_reference",
                            source="s_weather.data.city"),
        ),
    ))
    actual = DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=(_step(
            "s2", "nearby.search",
            slots={"category": "室内", "sort": "rating", "radius": 3000,
                   "weather_context": "雨"},
            slot_refs={"location": "s_weather.data.city"}),),
            complexity="simple", goal="", skills=(), exemplars=(),
            hint_effect="", catalog_stats={}),
    )
    assert judge_turn(expected, actual).passed


def test_decision_and_ingress_are_independent_from_plan():
    expected = TurnExpectation(
        addressed=True,
        ingress_allowed=("cloud",),
        ingress_forbidden=("edge_local",),
        decision_allowed=("clarify",),
        clarify="required",
    )
    actual = DecisionSnapshot(ingress="edge_local", addressed=True,
                              decision="execute", clarify=False,
                              plan=PlanSnapshot.empty())
    result = judge_turn(expected, actual)
    assert not result.passed
    assert {a.name for a in result.assertions if not a.passed} >= {
        "ingress_allowed", "decision_allowed", "clarify",
    }


def test_replan_groups_contribute_to_aggregate_recall():
    expected = TurnExpectation(
        plan=PlanExpectation(
            assert_plan=True,
            required_groups=(IntentGroup(("info.weather",)),)),
        replans=(ReplanExpectation(
            after={"result": {"step_id": "s1", "status": "ok", "data": {}}},
            plan=PlanExpectation(
                assert_plan=True,
                required_groups=(IntentGroup(("nearby.search",)),))),))
    actual = _snapshot("info.weather")
    actual = DecisionSnapshot(
        ingress=actual.ingress, addressed=actual.addressed,
        decision=actual.decision, clarify=actual.clarify, plan=actual.plan,
        replans=(_snapshot("chitchat.talk").plan,))
    assert judge_turn(expected, actual).metric("required_group_recall") == 0.5


# ── 最小对照关系 ───────────────────────────────────────────────────────────
from support.intent_adversarial_contract import RelationSpec  # noqa: E402
from support.intent_adversarial_judge import (  # noqa: E402
    judge_relation, semantic_signature,
)


def test_invariant_requires_same_semantic_signature():
    base = _snapshot("info.weather")
    variant = _snapshot("info.weather")
    assert judge_relation(RelationSpec("base", "invariant", {}), base, variant).passed


def test_route_flip_requires_changed_signature_and_declared_forbidden_after():
    base = _snapshot("charging.find")
    variant = _snapshot("chitchat.talk")
    spec = RelationSpec("base", "route_flip", {"forbidden_after": ["charging.find"],
                                               "required_change": True})
    assert judge_relation(spec, base, variant).passed


def test_intent_add_requires_set_delta():
    spec = RelationSpec("base", "intent_add", {"add": ["info.weather"]})
    assert judge_relation(spec, _snapshot("reminder.create"),
                          _snapshot("reminder.create", "info.weather")).passed


def test_intent_remove_requires_set_delta():
    spec = RelationSpec("base", "intent_remove", {"remove": ["info.weather"]})
    assert judge_relation(spec, _snapshot("reminder.create", "info.weather"),
                          _snapshot("reminder.create")).passed


def test_clarify_flip_requires_decision_change():
    base = DecisionSnapshot("cloud", True, "clarify", True, PlanSnapshot.empty())
    variant = _snapshot("navigation.navigate_to")
    assert judge_relation(RelationSpec("base", "clarify_flip", {}), base, variant).passed


def test_context_override_uses_variant_absolute_result():
    spec = RelationSpec("base", "context_override", {"must_differ": True})
    assert judge_relation(spec, _snapshot("info.weather"),
                          _snapshot("nearby.search")).passed


def test_clause_commute_ignores_step_order_but_not_intent_set():
    spec = RelationSpec("base", "clause_commute", {})
    assert judge_relation(spec, _snapshot("info.weather", "reminder.create"),
                          _snapshot("reminder.create", "info.weather")).passed
    assert not judge_relation(spec, _snapshot("info.weather", "reminder.create"),
                              _snapshot("reminder.create", "info.news")).passed


def test_semantic_signature_ignores_step_ids_but_keeps_dependency_semantics():
    linked = DecisionSnapshot("cloud", True, "execute", False, PlanSnapshot(
        steps=(_step("s1", "info.weather"),
               _step("s2", "nearby.search", depends_on=("s1",))),
        complexity="simple", goal="", skills=(), exemplars=(), hint_effect="",
        catalog_stats={}))
    renamed = DecisionSnapshot("cloud", True, "execute", False, PlanSnapshot(
        steps=(_step("a", "info.weather"),
               _step("b", "nearby.search", depends_on=("a",))),
        complexity="simple", goal="", skills=(), exemplars=(), hint_effect="",
        catalog_stats={}))
    unlinked = DecisionSnapshot("cloud", True, "execute", False, PlanSnapshot(
        steps=(_step("s1", "info.weather"), _step("s2", "nearby.search")),
        complexity="simple", goal="", skills=(), exemplars=(), hint_effect="",
        catalog_stats={}))
    assert semantic_signature(linked) == semantic_signature(renamed)
    assert semantic_signature(linked) != semantic_signature(unlinked)


# ── 检索期望 ───────────────────────────────────────────────────────────────
from dataclasses import replace as _replace  # noqa: E402

from support.intent_adversarial_contract import RetrievalExpectation  # noqa: E402


def test_retrieval_expectation_checks_required_and_forbidden_assets():
    expected = TurnExpectation(retrieval=RetrievalExpectation(
        required_skills=("weather-outing",), forbidden_exemplars=("nearby#bad",)))
    actual = _snapshot("nearby.search")
    actual = _replace(actual, plan=_replace(
        actual.plan, skills=("full:weather-outing@lex:1.0",), exemplars=()))
    assert judge_turn(expected, actual).passed


def test_retrieval_names_are_compared_after_stripping_mode_channel_and_score():
    expected = TurnExpectation(retrieval=RetrievalExpectation(
        required_exemplars=("nearby#23",)))
    actual = _snapshot("nearby.search")
    actual = _replace(actual, plan=_replace(
        actual.plan, exemplars=("shadow:nearby#23@vec:0.81",)))
    assert judge_turn(expected, actual).passed


def test_forbidden_retrieval_asset_fails_even_when_the_plan_is_right():
    expected = TurnExpectation(
        plan=PlanExpectation(assert_plan=True,
                             required_groups=(IntentGroup(("nearby.search",)),)),
        retrieval=RetrievalExpectation(forbidden_skills=("weather-outing",)))
    actual = _snapshot("nearby.search")
    actual = _replace(actual, plan=_replace(
        actual.plan, skills=("full:weather-outing@lex:0.6",)))
    result = judge_turn(expected, actual)
    assert not result.passed
    assert any(a.name == "retrieval.forbidden:weather-outing" and not a.passed
               for a in result.assertions)


def test_empty_retrieval_expectation_produces_no_assertion():
    result = judge_turn(TurnExpectation(), _snapshot("info.weather"))
    assert not [a for a in result.assertions if a.name.startswith("retrieval.")]


def test_clipped_asset_never_satisfies_a_required_retrieval():
    expected = TurnExpectation(retrieval=RetrievalExpectation(
        required_skills=("charging-strategy",)))
    actual = _snapshot("charging.find")
    actual = _replace(actual, plan=_replace(
        actual.plan, skills=("full:charging-strategy@lex:23!clipped",)))
    result = judge_turn(expected, actual)
    assert not result.passed
    failed = [a for a in result.assertions
              if a.name == "retrieval.required:charging-strategy"]
    assert failed and "clipped" in failed[0].detail


def test_clipped_asset_does_not_count_as_a_forbidden_hit_either():
    expected = TurnExpectation(retrieval=RetrievalExpectation(
        forbidden_skills=("weather-outing",)))
    actual = _snapshot("info.weather")
    actual = _replace(actual, plan=_replace(
        actual.plan, skills=("full:weather-outing@lex:3!clipped",)))
    assert judge_turn(expected, actual).passed


# ── engine 期望：只有完整决策链观测得到的三项 ────────────────────────────


def _engine_snapshot(*, agent_calls=(), pending=None, observed=True,
                     side_effects=()):
    return _replace(_snapshot("nearby.order"), engine_observed=observed,
                    agent_calls=tuple(agent_calls), pending_confirm_after=pending,
                    side_effects=tuple(side_effects))


def _engine_expectation(**changes):
    from support.intent_adversarial_contract import EngineExpectation
    return TurnExpectation(engine=EngineExpectation(declared=True, **changes))


def test_declaring_engine_gold_but_never_reaching_the_engine_is_a_failure():
    """**没观测到 Engine 是失败，不是「不适用」。**

    旧实现在 `engine_observed=False` 时直接 return，整组 Engine gold 被静默跳过：
    一条声明了 `required_agent_calls` + `pending_confirm_after` 的用例，实际在 Edge
    本地就结束了、根本没到 Engine，仍然只留下 decision/replan/safety 三条绿断言整轮
    通过——**最需要这组断言的那一刻正好是它失效的那一刻**（评审 P0-B）。
    """
    expected = _engine_expectation(forbidden_agent_calls=("nearby.order",),
                                   pending_confirm_after=True)
    unobserved = _engine_snapshot(agent_calls=("nearby.order",), observed=False)
    result = judge_turn(expected, unobserved)
    observed = [a for a in result.assertions if a.name == "engine.observed"]
    assert observed and not observed[0].passed
    assert not result.passed
    # 未观测时不再往下裁具体那几条——它们没有事实可依，报「没走到 Engine」就够了
    assert not [a for a in result.assertions
                if a.name.startswith("engine.") and a.name != "engine.observed"]


def test_no_engine_gold_means_no_engine_assertions_at_all():
    """反向：没声明 `expected.engine` 的用例不许被这条闸误伤（L0/L1 全在此列）。"""
    from support.intent_adversarial_contract import TurnExpectation

    result = judge_turn(TurnExpectation(), _engine_snapshot(observed=False))
    assert not [a for a in result.assertions if a.name.startswith("engine.")]


def test_forbidden_agent_call_fires_even_when_no_side_effect_landed():
    """确认闸拦住了执行，但那个 Agent 已经被够着了——副作用面看不见这件事。"""
    expected = _engine_expectation(forbidden_agent_calls=("nearby.order",))
    snapshot = _engine_snapshot(agent_calls=("nearby.order",))
    result = judge_turn(expected, snapshot)
    assert snapshot.side_effects == ()
    assert not result.passed
    assert result.metrics["forbidden_agent_call_count"] == 1.0


def test_pending_state_after_the_turn_is_asserted_three_ways():
    expected = _engine_expectation(pending_confirm_after=True)
    assert judge_turn(expected, _engine_snapshot(pending=True)).passed
    assert not judge_turn(expected, _engine_snapshot(pending=False)).passed
    # 观测不到时是 None——不能当成 False 判红，也不能当成 True 放行
    assert not judge_turn(expected, _engine_snapshot(pending=None)).passed


def test_repeat_execution_limit_needs_more_than_one_turn_to_be_provable():
    expected = _engine_expectation(max_agent_calls_per_intent=1)
    once = _engine_snapshot(agent_calls=("nearby.order",))
    twice = _engine_snapshot(agent_calls=("nearby.order", "nearby.order"))
    assert judge_turn(expected, once).passed
    assert not judge_turn(expected, twice).passed


def test_semantic_signature_separates_runs_that_called_different_agents():
    from support.intent_adversarial_judge import semantic_signature

    quiet = _engine_snapshot(agent_calls=(), pending=True)
    noisy = _engine_snapshot(agent_calls=("nearby.order",), pending=True)
    assert semantic_signature(quiet) != semantic_signature(noisy)


# ── 独立复审 §8 P1-A：指标把「未断言」写成绿 ──────────────────────────────


def test_no_plan_gold_means_no_plan_metrics_at_all():
    """反向构造：一条**根本没有 plan gold** 的用例（L0 全在此列）。

    旧实现无条件写 recall=1 / forbidden=0 / overroute=0 / dependency=1，于是 L0 的
    `required_group_recall=70/70 100%` 量的不是召回，是「有 70 个证据单元」。
    分母为 0 的地方要显示 null，不是 100%。
    """
    bare = judge_turn(TurnExpectation(), _snapshot("info.weather"))
    for name in ("required_group_recall", "forbidden_route_count",
                 "overroute_count", "dependency_pass"):
        assert name not in bare.metrics, f"{name} 不该在没有 gold 时被写出来"

    # 反向：写了 plan gold 就必须有这几个数，否则这条闸只是把指标删光
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    with_gold = judge_turn(expected, _snapshot("info.weather"))
    assert with_gold.metrics["required_group_recall"] == 1.0
    assert with_gold.metrics["overroute_count"] == 0.0
    assert with_gold.metrics["forbidden_route_count"] == 0.0


def test_an_extra_replan_breaks_the_exact_plan_set():
    """反向构造：计划集合本身对，但**多规划了一轮 replan**。

    `exact_plan_set` 的子集原来只匹配 `plan.` / `replan[`，不含 `replan_count`——
    实测有 turn 因多出一次 replan 而失败，这个指标仍记 1。
    """
    expected = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    clean = _snapshot("info.weather")
    extra = DecisionSnapshot(
        ingress=clean.ingress, addressed=True, decision="execute", clarify=False,
        plan=clean.plan, replans=(_snapshot("reminder.create").plan,))

    assert judge_turn(expected, clean).subset_passed(
        "plan.", "replan[", "replan_count") is True
    judged = judge_turn(expected, extra)
    assert not judged.passed
    assert judged.subset_passed("plan.", "replan[", "replan_count") is False

    # 没有 plan gold 时这条断言压根不存在——否则整个 L0 又被拖进 exact 的分母
    assert judge_turn(TurnExpectation(), clean).subset_passed(
        "plan.", "replan[", "replan_count") is None
