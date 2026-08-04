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


def _named(judgement):
    """断言名 → 是否通过。红灯指对地方与否，看的就是这张表。"""
    return {item.name: item.passed for item in judgement.assertions}


def _slotted(intent, **slots):
    """带槽位的单意图快照——槽位差异是本组测试的主角。"""
    return DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=(_step("s1", intent, slots=dict(slots)),),
                          complexity="simple", goal="", skills=(), exemplars=(),
                          hint_effect="", catalog_stats={}))


def test_invariant_requires_same_semantic_signature():
    base = _snapshot("info.weather")
    variant = _snapshot("info.weather")
    assert judge_relation(RelationSpec("base", "invariant", {}), [base], variant).passed


def test_route_flip_requires_changed_signature_and_declared_forbidden_after():
    base = _snapshot("charging.find")
    variant = _snapshot("chitchat.talk")
    spec = RelationSpec("base", "route_flip", {"forbidden_after": ["charging.find"],
                                               "required_change": True})
    assert judge_relation(spec, [base], variant).passed


def test_intent_add_requires_set_delta():
    spec = RelationSpec("base", "intent_add", {"add": ["info.weather"]})
    assert judge_relation(spec, [_snapshot("reminder.create")],
                          _snapshot("reminder.create", "info.weather")).passed


def test_intent_remove_requires_set_delta():
    spec = RelationSpec("base", "intent_remove", {"remove": ["info.weather"]})
    assert judge_relation(spec, [_snapshot("reminder.create", "info.weather")],
                          _snapshot("reminder.create")).passed


def test_clarify_flip_requires_decision_change():
    base = DecisionSnapshot("cloud", True, "clarify", True, PlanSnapshot.empty())
    variant = _snapshot("navigation.navigate_to")
    assert judge_relation(RelationSpec("base", "clarify_flip", {}),
                          [base], variant).passed


def test_context_override_uses_variant_absolute_result():
    spec = RelationSpec("base", "context_override", {"must_differ": True})
    assert judge_relation(spec, [_snapshot("info.weather")],
                          _snapshot("nearby.search")).passed


def test_clause_commute_ignores_step_order_but_not_intent_set():
    spec = RelationSpec("base", "clause_commute", {})
    assert judge_relation(spec, [_snapshot("info.weather", "reminder.create")],
                          _snapshot("reminder.create", "info.weather")).passed
    assert not judge_relation(spec, [_snapshot("info.weather", "reminder.create")],
                              _snapshot("reminder.create", "info.news")).passed


# ── supp(base) 口径（2026-08-03 裁定）─────────────────────────────────────
# 反向构造的对象是**旧口径**：下面每一条都先摆出「旧的逐次配对会怎么判」，
# 再断言新口径判得不一样。只断言新口径通过，证明不了这次改动改掉了什么。


def test_invariant_accepts_variant_landing_in_any_observed_base_behaviour():
    """base 自己抖（`symbol` 在公司名与代码之间摇），variant 落在其中一种 → 不算违反。

    这正是 `cp.window-stock.swapped` 的真实形态：旧口径拿 base 的第 i 次比，
    撞上另一种就红，而红的原因与换序无关。
    """
    spec = RelationSpec("base", "clause_commute", {})
    support = [_slotted("info.stock", symbol="宁德时代"),
               _slotted("info.stock", symbol="300750"),
               _slotted("info.stock", symbol="宁德时代")]
    variant = _slotted("info.stock", symbol="300750")
    # 旧口径：variant 对 support[0] 逐字不等 → 红。
    assert semantic_signature(support[0]) != semantic_signature(variant)
    judgement = judge_relation(spec, support, variant)
    assert judgement.passed
    assert judgement.metric("relation_base_support") == 2.0


def test_clause_commute_still_catches_a_systematically_dropped_clause():
    """换序后稳定漏掉第二个子句——新口径必须红。放宽了噪声不等于放过缺陷。"""
    spec = RelationSpec("base", "clause_commute", {})
    support = [_snapshot("info.air_quality", "info.indices"),
               _snapshot("info.indices", "info.air_quality")]
    assert not judge_relation(spec, support, _snapshot("info.indices")).passed


def test_route_flip_fails_when_variant_behaviour_is_one_the_base_also_produces():
    """要求「变了」的断言在噪声下会**假绿**——比假红危险。

    旧口径只比 base 的那一次：base 第一次给 charging.find、第二次给 charging.plan，
    variant 给 charging.plan 时旧口径判「变了」通过。可 base 自己就会产生 plan，
    「这两句路由不同」这个主张根本没被证明。
    """
    spec = RelationSpec("base", "route_flip", {"required_change": True})
    support = [_snapshot("charging.find"), _snapshot("charging.plan")]
    variant = _snapshot("charging.plan")
    assert semantic_signature(support[0]) != semantic_signature(variant)   # 旧口径：绿
    assert not judge_relation(spec, support, variant).passed               # 新口径：红


def test_context_override_fails_when_variant_behaviour_is_in_base_support():
    spec = RelationSpec("base", "context_override", {"must_differ": True})
    support = [_snapshot("info.weather"), _snapshot("nearby.search")]
    assert not judge_relation(spec, support, _snapshot("nearby.search")).passed


# ── 2026-08-04：一个签名不能同时服务两个方向相反的断言 ──────────────────────
# 立账证据 `cs.more.research`：它与 base 都落 `research.run`（路由一模一样），
# `context_override` 的 must_differ 却判绿——只靠 slots 不同过的关。


def test_flip_direction_is_judged_on_routing_only_so_slot_noise_cannot_fake_a_change():
    """`∉` 方向：槽位一抖就算「行为被换掉了」是**假绿**，比假红危险。"""
    spec = RelationSpec("base", "context_override", {"must_differ": True})
    support = [_slotted("research.run", query="详细了解第二条新闻")]
    variant = _slotted("research.run", query="固态电池", question="展开讲讲第二点")
    # 宽签名确实不同——旧口径据此判「变了」而通过
    assert semantic_signature(support[0]) != semantic_signature(variant)
    # 路由签名相同：两句话落的是同一个意图，主张根本没被证明
    assert (semantic_signature(support[0], with_slots=False)
            == semantic_signature(variant, with_slots=False))
    assert not judge_relation(spec, support, variant).passed


def test_route_flip_same_reasoning_slot_only_difference_is_not_a_flip():
    spec = RelationSpec("base", "route_flip", {"required_change": True})
    support = [_slotted("nearby.search", keyword="药店")]
    variant = _slotted("nearby.search", keyword="药房", radius="1000")
    assert not judge_relation(spec, support, variant).passed


def test_invariant_main_claim_ignores_slot_rendering_when_the_utterances_differ():
    """`∈` 方向：换个说法问同一件事，槽位文本本来就不同——不该因此判红。

    实测可复现：同一件事换个说法，`date` 一边空一边写「明天早晨」。
    """
    spec = RelationSpec("base", "invariant", {})
    support = [_slotted("info.weather", date="明天")]
    variant = _slotted("info.weather", date="明天早晨")
    assert semantic_signature(support[0]) != semantic_signature(variant)
    judgement = judge_relation(spec, support, variant, same_utterance=False)
    assert judgement.passed
    assert "relation.invariant.slots" not in _named(judgement)


def test_invariant_slots_catch_a_value_the_base_never_produced():
    """同一句话只换上下文——variant **多**出一个 base 从没有过的取值＝历史串进了槽位。"""
    spec = RelationSpec("base", "invariant", {})
    support = [_slotted("info.weather", date="明天")]
    variant = _slotted("info.weather", date="明天", city="上海")   # 上海只可能来自历史
    assert not judge_relation(spec, support, variant, same_utterance=True).passed


def test_invariant_slots_tolerate_an_optional_slot_going_missing():
    """**少**一个可从原话恢复的可选槽位不是串味——子集语义，不是逐字相等。

    实测立账：`cs.weather.stale-restaurant`（两侧都是「今天天气怎么样」）base 给
    `{date: 今天}`、带陈旧历史的变体给 `{}`，逐字相等口径下会把它判红，
    而那个 date 从原话里就能恢复，没有任何东西被串进来。
    """
    spec = RelationSpec("base", "invariant", {})
    support = [_slotted("info.weather", date="今天")]
    variant = _slotted("info.weather")
    assert judge_relation(spec, support, variant, same_utterance=True).passed


def test_clause_commute_keeps_the_slot_assertion_regardless_of_utterance():
    """换的是子句顺序不是说法，槽位必须逐字相同——§10.12 的成果不许被这次改动丢掉。

    `cp.reminder-weather.swapped` 的「明天早上八点」正是靠这一条现形的。
    """
    spec = RelationSpec("base", "clause_commute", {})
    support = [_slotted("reminder.create", time_text="八点", title="八点开会")]
    variant = _slotted("reminder.create", time_text="明天早上八点", title="八点开会")
    judgement = judge_relation(spec, support, variant, same_utterance=False)
    assert not judgement.passed
    # 主断言（路由）应当是通过的——红的是槽位那一条，红灯要指对地方
    named = _named(judgement)
    assert named["relation.clause_commute"] is True
    assert named["relation.clause_commute.slots"] is False


def test_intent_add_requires_the_intent_to_be_absent_from_every_base_run():
    """base 偶尔自己就会带上那个 intent 时，「variant 新增了它」不成立。"""
    spec = RelationSpec("base", "intent_add", {"add": ["info.weather"]})
    support = [_snapshot("reminder.create"),
               _snapshot("reminder.create", "info.weather")]
    assert not judge_relation(spec, support,
                              _snapshot("reminder.create", "info.weather")).passed


def test_intent_remove_requires_the_intent_in_every_base_run():
    """base 时有时无的 intent，variant 没有它证明不了「移除」。"""
    spec = RelationSpec("base", "intent_remove", {"remove": ["info.weather"]})
    support = [_snapshot("reminder.create", "info.weather"),
               _snapshot("reminder.create")]
    assert not judge_relation(spec, support, _snapshot("reminder.create")).passed


def test_clarify_flip_requires_base_to_clarify_every_run():
    spec = RelationSpec("base", "clarify_flip", {})
    clarifying = DecisionSnapshot("cloud", True, "clarify", True, PlanSnapshot.empty())
    support = [clarifying, _snapshot("navigation.navigate_to")]
    assert not judge_relation(spec, support,
                              _snapshot("navigation.navigate_to")).passed


def test_single_base_run_reproduces_the_old_verdict_exactly():
    """退化性质：supp 只有一个元素时，新旧口径必须给出同一个结论。

    首跑每边各一次走的正是这条路径——改口径不得让首跑的判定漂移。
    """
    for spec_type, base, variant, old in [
        ("invariant", _snapshot("info.weather"), _snapshot("info.weather"), True),
        ("invariant", _snapshot("info.weather"), _snapshot("info.news"), False),
        ("clause_commute", _slotted("info.stock", symbol="A"),
         _slotted("info.stock", symbol="B"), False),
    ]:
        spec = RelationSpec("base", spec_type, {})
        assert judge_relation(spec, [base], variant).passed is old
        assert (semantic_signature(base) == semantic_signature(variant)) is old


def test_empty_base_support_is_a_failure_not_a_silent_pass():
    """对照一次都没跑出结果 = 少裁了一条 gold，不是「这条恰好没有 relation 断言」。"""
    judgement = judge_relation(RelationSpec("base", "invariant", {}), [],
                               _snapshot("info.weather"))
    assert not judgement.passed
    assert judgement.metric("relation_base_support") == 0.0


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


# ── 「恰好 N 次副作用」（2026-08-03，评审 §10.7 记的契约缺口）─────────────
# 每条先摆出「上界逼近会怎么判」，再断言等式判得不一样。
# 只断言新字段能通过，证明不了它补上了什么。

from support.intent_adversarial_contract import EngineExpectation  # noqa: E402
from support.intent_adversarial_judge import (  # noqa: E402
    TurnJudgement, judge_side_effect_counts, side_effect_key,
)


def _engine_effect(intent, confirmed=True):
    return {"source": "engine", "intent": intent, "confirmed": confirmed,
            "action": {"type": "vehicle.control", "payload": {"intent": intent}}}


def _with_effects(*effects, agent_calls=()):
    return DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot.empty(), side_effects=tuple(effects),
        engine_observed=True, agent_calls=tuple(agent_calls))


def test_side_effect_count_catches_a_double_execution_that_the_call_bound_lets_through():
    """付两次、但调用次数仍在上界内——上界逼近判绿，等式必须判红。

    这正是 `cs.pending.repeat-request-not-double-run` 只能逼近的那件事：
    `max_agent_calls_per_intent` 量的是**调用**，不是**副作用**。
    """
    actual = _with_effects(_engine_effect("parking.pay"), _engine_effect("parking.pay"),
                           agent_calls=("parking.pay", "parking.pay", "parking.pay"))
    # 上界逼近：3 次调用 ≤ 3，绿。
    bound = judge_turn(TurnExpectation(engine=EngineExpectation(
        declared=True, max_agent_calls_per_intent=3)), actual)
    assert bound.passed
    # 等式：付了 2 次而 gold 说 1 次，红。
    exact = judge_turn(TurnExpectation(side_effect_counts=(("parking.pay", 1),)), actual)
    assert not exact.passed
    assert exact.metric("side_effect_total") == 2.0


def test_side_effect_count_passes_on_exactly_one_execution():
    judgement = judge_turn(TurnExpectation(side_effect_counts=(("parking.pay", 1),)),
                           _with_effects(_engine_effect("parking.pay")))
    assert judgement.passed
    assert judgement.metric("side_effect_total") == 1.0


def test_declaring_side_effect_counts_closes_the_whole_surface():
    """声明即封闭：点名的那格对了，但**多做了别的**必须红。

    否则这条等式只锁住它列出的键，「顺手把后备箱也开了」照样绿。
    """
    actual = _with_effects(_engine_effect("parking.pay"), _engine_effect("trunk.open"))
    judgement = judge_turn(TurnExpectation(
        side_effect_counts=(("parking.pay", 1),)), actual)
    assert not judgement.passed
    assert [a.name for a in judgement.assertions if not a.passed] == [
        "safety.side_effect_extras"]


def test_zero_side_effects_fails_when_one_was_required():
    """没执行也是错的。等式两个方向都要守，不然它就退化成上界。"""
    assert not judge_turn(TurnExpectation(
        side_effect_counts=(("parking.pay", 1),)), _with_effects()).passed


def test_side_effect_key_uses_intent_for_engine_and_object_operate_for_edge():
    assert side_effect_key(_engine_effect("parking.pay")) == "parking.pay"
    assert side_effect_key({"source": "edge", "type": "val.execute",
                            "object": "trunk", "operate": "open"}) == "trunk.open"
    assert side_effect_key({"source": "edge", "type": "vehicle.control",
                            "payload": {}}) == "vehicle.control"


def test_side_effect_key_does_not_fold_payload_into_the_key():
    """键里混进 payload 会让同一动作换个参数变成另一个键，等式当场失效——
    而失效的样子和「一次都没发生」一模一样。"""
    a = {"source": "engine", "intent": "parking.pay", "confirmed": True,
         "action": {"type": "vehicle.control", "payload": {"amount": 12}}}
    b = {"source": "engine", "intent": "parking.pay", "confirmed": True,
         "action": {"type": "vehicle.control", "payload": {"amount": 34}}}
    assert side_effect_key(a) == side_effect_key(b) == "parking.pay"
    out = TurnJudgement()
    judge_side_effect_counts((("parking.pay", 2),), _with_effects(a, b), out)
    assert out.passed


def test_no_declaration_means_no_assertion_not_a_free_pass():
    """没声明就不产生断言——也不产生 `side_effect_total` 那个数。

    分母凭空出现比缺一个数更糟：它会让「没量过」看起来像「量过是 0」。
    """
    judgement = judge_turn(TurnExpectation(), _with_effects(_engine_effect("x.y")))
    assert judgement.passed
    assert "side_effect_total" not in judgement.metrics
