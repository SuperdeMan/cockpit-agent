"""Business scope is grounded in the original input and cannot become execution authority."""
import copy

import pytest

from orchestrator.cloud.models import Plan, PlanContext, SessionState, Step, step_raw_text, step_record
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.step_input import bind_step_inputs, source_spans

TEXT = "打开后备箱，再告诉我空调有哪些模式"


def plan(text=TEXT):
    return Plan(steps=[
        Step("s1", "edge", intent="trunk.open", origin_text=text,
             capability_description="打开后备箱", require_confirm=True),
        Step("s2", "manual-rag", intent="manual.query", origin_text=text,
             capability_description="车型手册", slots={"question": "空调有哪些模式"}, response_only=True),
    ], raw_text=text, safety_origin_text=text, origin_exchange_id="turn-1")


def test_mixed_question_and_action_have_separate_inputs_but_keep_full_safety_origin():
    p = plan()
    bind_step_inputs(p, origin_exchange_id="turn-1")
    ctx = PlanContext(raw_text=TEXT, safety_origin_text=TEXT)
    assert step_raw_text(p.steps[0], ctx) == "打开后备箱"
    assert step_raw_text(p.steps[1], ctx) == "告诉我空调有哪些模式"
    assert p.safety_origin_text == ctx.safety_origin_text == TEXT
    assert all(s.origin_text == TEXT for s in p.steps)
    assert p.steps[1].input_scope["origin_exchange_id"] == "turn-1"


def test_restored_scope_survives_but_slot_answer_belongs_to_resumed_step():
    p = plan()
    bind_step_inputs(p, origin_exchange_id="turn-1")
    state = SessionState(phase="wait_confirm", pending_step_id="s1",
                         pending_plan=PlannerEngine._serialize_plan(p))
    restored, _ = PlannerEngine._restore(None, state, inject_confirmed=False)
    assert restored.origin_exchange_id == "turn-1"
    assert restored.steps[1].input_scope == p.steps[1].input_scope
    ctx = PlanContext(raw_text="取消")
    assert step_raw_text(restored.steps[0], ctx) == "取消"
    assert step_raw_text(restored.steps[1], ctx) == "告诉我空调有哪些模式"
    assert not restored.steps[0].meta.get("confirmed")


@pytest.mark.parametrize("text", [
    "打开后备箱，再告诉我空调有哪些模式，别丢掉说明书里的注意事项",
    "如果下雨就打开后备箱，再告诉我空调有哪些模式",
    "打开后备箱，再告诉我‘空调有哪些模式’的意思",
])
def test_unowned_constraint_condition_or_quote_keeps_the_complete_utterance(text):
    p = plan(text)
    bind_step_inputs(p)
    assert all(step_raw_text(s, PlanContext(raw_text=text)) == text for s in p.steps)


def test_whole_utterance_and_ambiguous_owners_are_not_split():
    p = plan()
    p.steps[0].whole_utterance = True
    bind_step_inputs(p)
    assert all(s.input_scope["basis"] == "utterance" for s in p.steps)
    p = plan()
    p.steps[1].slots = {}
    p.steps[1].capability_description = "打开后备箱"
    bind_step_inputs(p)
    assert all(s.input_scope["basis"] == "utterance" for s in p.steps)


@pytest.mark.parametrize("bad", [[], [{"start": -1, "end": 5}], [{"start": True, "end": 5}],
                                   [{"start": 0, "end": 1000}], [{"start": 6, "end": 2}]])
def test_malformed_persisted_ranges_cannot_replace_the_origin(bad):
    p = plan()
    bind_step_inputs(p)
    s = p.steps[1]
    s.input_scope["spans"] = bad
    assert step_raw_text(s, PlanContext(raw_text="无关槽答案")) == TEXT


def test_changed_origin_invalidates_binding_and_legacy_record_needs_no_scope():
    p = plan()
    bind_step_inputs(p)
    s = p.steps[1]
    s.origin_text = "解释座椅加热是什么意思"
    assert step_raw_text(s, PlanContext()) == s.origin_text
    old = Step("s0", "a", origin_text=TEXT)
    assert "input_scope" not in step_record(old)
    assert step_raw_text(old, PlanContext()) == TEXT


def test_offsets_reference_actual_unicode_source_and_binding_is_not_replaced():
    text = "  😀打开后备箱， 再告诉我空调有哪些模式 "
    spans = source_spans(text)
    assert [text[s["start"]:s["end"]] for s in spans] == ["😀打开后备箱", "告诉我空调有哪些模式"]
    p = plan()
    bind_step_inputs(p, origin_exchange_id="turn-1")
    saved = copy.deepcopy(p.steps[1].input_scope)
    bind_step_inputs(p, origin_exchange_id="unrelated-turn")
    assert p.steps[1].input_scope == saved


def test_legacy_origin_without_a_turn_binding_is_not_reinterpreted():
    p = plan()
    p.origin_exchange_id = ""
    bind_step_inputs(p)
    assert all(not s.input_scope for s in p.steps)
    assert step_raw_text(p.steps[1], PlanContext(raw_text="别的答案")) == TEXT


def test_scope_in_model_json_is_not_accepted_by_capability_validation():
    from tests.test_planning import MockAgent
    from orchestrator.cloud.planning import PlanBuilder
    agent = MockAgent("info", ["info.weather"])
    steps = PlanBuilder._validated_steps([{
        "id": "s1", "agent_id": "info", "intent": "info.weather", "slots": {},
        "input_scope": {"basis": "clauses", "origin_exchange_id": "forged"},
        "origin_text": "forged", "origin_exchange_id": "forged",
    }], {"info": agent})
    assert len(steps) == 1 and not steps[0].input_scope and not steps[0].origin_text
