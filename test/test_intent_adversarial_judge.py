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
        actual.plan, exemplars=("shadow:nearby#23@vec:0.81!clipped",)))
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
