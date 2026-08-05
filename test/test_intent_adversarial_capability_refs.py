"""Raw-evidence and validator boundaries for planner capability references."""
from __future__ import annotations

import asyncio
import json
import sys
from types import MappingProxyType, SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.cloud import planning  # noqa: E402
from orchestrator.cloud.context import WorkingSet  # noqa: E402
from orchestrator.cloud.models import Plan, PlanContext, Step  # noqa: E402
from support.intent_adversarial_trace import (  # noqa: E402
    RecordingPlanner,
    TraceSink,
    attach_validation_trace,
)


_INVALID = "__invalid_capability_reference__"
_MISSING = object()


def _agent(agent_id: str, *intents: str):
    caps = [SimpleNamespace(intent=intent, slots=[], description="", examples=[],
                            heavy=False, require_confirm=False, verification=None)
            for intent in intents]
    manifest = SimpleNamespace(
        agent_id=agent_id, capabilities=caps, latency_budget_ms=5000,
        kind="agent", deployment="cloud", requires_permissions=[],
        trust_level="first_party", context_scopes=[], route_hints=[], category="",
    )
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50051")


def _catalog(agent):
    return SimpleNamespace(
        ref_to_pair=MappingProxyType({"cap_0001": (agent.manifest.agent_id,
                                                   agent.manifest.capabilities[0].intent)}),
        pair_to_ref=MappingProxyType({(agent.manifest.agent_id,
                                       agent.manifest.capabilities[0].intent): "cap_0001"}),
        agent_map=MappingProxyType({agent.manifest.agent_id: agent}),
    )


def test_trace_restores_valid_intent_and_keeps_invalid_ref_sentinel():
    agent = _agent("alpha", "alpha.one")
    catalog = _catalog(agent)

    class Builder:
        def _parse_and_validate_data(self, wire, _catalog, _text):
            ref = ((wire.get("steps") or [{}])[0].get("capability_ref")
                   if isinstance(wire, dict) and wire.get("steps") else None)
            if ref == "cap_0001":
                return Plan(steps=[Step(id="s1", agent_id="alpha",
                                        endpoint=agent.endpoint, intent="alpha.one")])
            return None

    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    wires = [
        {"steps": [{"id": "s1", "capability_ref": "cap_0001", "slots": {}}]},
        {"steps": [{"id": "s1", "capability_ref": "cap_9999", "slots": {}}]},
        {"steps": [{"id": "s1", "slots": {}}]},
        {"steps": [{"id": "s1", "capability_ref": 7, "slots": {}}]},
        {"steps": [{"id": "s1", "agent_id": "alpha", "intent": "alpha.one"}]},
    ]
    for wire in wires:
        builder._parse_and_validate_data(wire, catalog, "test")

    assert sink.trace_errors == []
    assert [trace.raw_intents for trace in sink.validations] == [
        ("alpha.one",), (_INVALID,), (_INVALID,), (_INVALID,), (_INVALID,),
    ]
    assert sink.validations[0].raw_candidate.intents == ("alpha.one",)
    assert all(trace.raw_candidate.intents == (_INVALID,)
               for trace in sink.validations[1:])


def test_trace_failure_is_explicit_and_cannot_look_like_observed_empty_raw():
    agent = _agent("alpha", "alpha.one")

    class ExplodingRefs:
        def get(self, _key, _default=None):
            raise RuntimeError("resolver exploded")

        def __getitem__(self, _key):
            raise RuntimeError("resolver exploded")

        def items(self):
            raise RuntimeError("resolver exploded")

        def values(self):
            raise RuntimeError("resolver exploded")

        def __iter__(self):
            raise RuntimeError("resolver exploded")

    catalog = SimpleNamespace(ref_to_pair=ExplodingRefs(), agent_map={"alpha": agent})

    class Builder:
        def _parse_and_validate_data(self, _wire, _catalog, _text):
            return None

    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    builder._parse_and_validate_data(
        {"steps": [{"capability_ref": "cap_0001"}]}, catalog, "test")
    assert sink.validations == []  # caller derives raw_observed=False
    assert sink.trace_errors and "resolver exploded" in sink.trace_errors[-1]


def test_trace_records_original_validator_failure_then_reraises_unchanged():
    agent = _agent("alpha", "alpha.one")
    catalog = _catalog(agent)
    marker = RuntimeError("production resolver failed")

    class Builder:
        def _parse_and_validate_data(self, _wire, _catalog, _text):
            raise marker

    builder, sink = Builder(), TraceSink()
    attach_validation_trace(builder, sink)
    with pytest.raises(RuntimeError) as caught:
        builder._parse_and_validate_data(
            {"steps": [{"capability_ref": "cap_0001"}]}, catalog, "test")
    assert caught.value is marker
    assert sink.validations == []
    assert sink.trace_errors and "validation_delegate:RuntimeError" in sink.trace_errors[-1]


def test_malformed_or_missing_steps_leave_sentinel_but_legal_omission_is_empty():
    agent = _agent("alpha", "alpha.one")
    catalog = _catalog(agent)

    async def noop(_messages):
        return ""

    builder = planning.PlanBuilder(noop, noop)
    sink = TraceSink()
    attach_validation_trace(builder, sink)
    invalid = [
        {"addressed": True, "steps": ""},
        {"addressed": True, "steps": 0},
        {"addressed": True, "steps": {}},
        {"addressed": True, "steps": None},
        {"addressed": True},
    ]
    for wire in invalid:
        assert builder._parse_and_validate_data(wire, catalog, "test") is None

    not_addressed = {"addressed": False}
    clarify = {
        "addressed": True,
        "clarify": {
            "question": "which?",
            "options": [
                {"label": "one", "send_text": "choose one"},
                {"label": "two", "send_text": "choose two"},
            ],
        },
    }
    assert builder._parse_and_validate_data(not_addressed, catalog, "test") is not None
    assert builder._parse_and_validate_data(
        {"addressed": False, "steps": []}, catalog, "test") is not None
    assert builder._parse_and_validate_data(clarify, catalog, "test") is not None

    addressed_malformed = {"addressed": False, "steps": None}
    clarify_malformed = {**clarify, "steps": None}
    assert builder._parse_and_validate_data(
        addressed_malformed, catalog, "test") is None
    assert builder._parse_and_validate_data(
        clarify_malformed, catalog, "test") is None

    assert [row.raw_intents for row in sink.validations[:5]] == [(_INVALID,)] * 5
    assert [row.result for row in sink.validations[:5]] == ["rejected"] * 5
    assert [row.raw_intents for row in sink.validations[5:8]] == [(), (), ()]
    assert [row.result for row in sink.validations[5:8]] == ["accepted"] * 3
    assert [row.raw_intents for row in sink.validations[8:]] == [(_INVALID,)] * 2
    assert [row.result for row in sink.validations[8:]] == ["rejected", "rejected"]


@pytest.mark.parametrize(("step", "expected_raw"), [
    ({"id": "s1", "capability_ref": "cap_9999"}, _INVALID),
    ({"id": "s1"}, _INVALID),
    ({"id": "s1", "agent_id": "alpha", "intent": "alpha.one"}, _INVALID),
    (7, _INVALID),
    ({"id": "s1", "capability_ref": "cap_0001"}, "alpha.one"),
])
def test_not_addressed_rejects_every_nonempty_action(step, expected_raw):
    agent = _agent("alpha", "alpha.one")
    catalog = _catalog(agent)

    async def noop(_messages):
        return ""

    builder = planning.PlanBuilder(noop, noop)
    sink = TraceSink()
    attach_validation_trace(builder, sink)

    wire = {"addressed": False, "steps": [step]}
    assert builder._parse_and_validate_data(wire, catalog, "test") is None
    assert len(sink.validations) == 1
    assert sink.validations[0].result == "rejected"
    assert sink.validations[0].raw_intents == (expected_raw,)


@pytest.mark.parametrize(("step", "displaced"), [
    pytest.param(
        {"id": "s1", "capability_ref": "cap_0001"},
        {
            "item": {"id": "s2", "capability_ref": "cap_0001"},
            "slots": {"value": "x"},
            "depends_on": ["s1"],
            "slot_refs": {"value": "s1.data.value"},
        },
        id="required-step-fields-displaced-to-top-level",
    ),
    pytest.param(
        {
            "id": "s1",
            "capability_ref": "cap_0001",
            "slots": {},
            "depends_on": [],
            "slot_refs": {},
            'capability_ref"': "cap_9999",
            "unexpected": "value",
        },
        {},
        id="complete-step-with-extra-fields",
    ),
])
def test_non_exact_step_shape_is_rejected_without_losing_valid_raw_intent(
        step, displaced):
    agent = _agent("alpha", "alpha.one")
    catalog = _catalog(agent)

    async def noop(_messages):
        return ""

    builder = planning.PlanBuilder(noop, noop)
    sink = TraceSink()
    attach_validation_trace(builder, sink)
    wire = {
        "addressed": True,
        "steps": [step],
        **displaced,
    }

    assert builder._parse_and_validate_data(wire, catalog, "test") is None
    assert builder._looks_like_no_action(wire) is False
    assert len(sink.validations) == 1
    trace = sink.validations[0]
    assert trace.result == "rejected"
    assert trace.raw_intents == ("alpha.one",)
    assert trace.raw_candidate.intents == ("alpha.one",)
    assert trace.accepted.steps == ()


def test_semantic_meaning_is_not_normalized_into_a_capability_ref():
    agent = _agent("alpha", "alpha.one")
    catalog = _catalog(agent)

    async def noop(_messages):
        return ""

    builder = planning.PlanBuilder(noop, noop)
    sink = TraceSink()
    attach_validation_trace(builder, sink)
    wire = {
        "addressed": True,
        "steps": [{
            "id": "s1",
            "capability_ref": "alpha.one",
            "slots": {},
            "depends_on": [],
            "slot_refs": {},
        }],
    }

    assert builder._parse_and_validate_data(wire, catalog, "test") is None
    assert len(sink.validations) == 1
    trace = sink.validations[0]
    assert trace.result == "rejected"
    assert trace.raw_intents == (_INVALID,)
    assert trace.raw_candidate.intents == (_INVALID,)
    assert trace.accepted.steps == ()


def test_two_empty_actions_avoid_fallback_but_invalid_nonempty_does_not(monkeypatch):
    agent = _agent("chitchat", "chitchat.talk")

    async def resolve(_query, top_k=1):
        return [agent]

    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")

    empty_calls = []

    async def empty_llm(_messages):
        empty_calls.append(True)
        return '{"addressed":true,"steps":[]}'

    empty_builder = planning.PlanBuilder(empty_llm, resolve)
    empty_fallbacks = []

    async def forbidden_fallback(*_args):
        empty_fallbacks.append(True)
        raise AssertionError("legal no-action must not call fallback")

    empty_builder._fallback = forbidden_fallback
    plan = asyncio.run(empty_builder.build(
        "先不用做", WorkingSet(catalog=[agent]), PlanContext(session_id="s")))
    assert plan.plan_mode.endswith("_no_action")
    assert empty_calls == [True, True]
    assert empty_fallbacks == []

    async def invalid_llm(_messages):
        return ('{"addressed":true,"steps":['
                '{"id":"s1","capability_ref":"cap_9999","slots":{}}]}')

    invalid_builder = planning.PlanBuilder(invalid_llm, resolve)
    fallbacks = []

    async def fallback(_text, _agents):
        fallbacks.append(True)
        return Plan(steps=[Step(id="fb", agent_id="chitchat",
                                endpoint=agent.endpoint, intent="chitchat.talk")])

    invalid_builder._fallback = fallback
    invalid_plan = asyncio.run(invalid_builder.build(
        "do missing", WorkingSet(catalog=[agent]), PlanContext(session_id="s")))
    assert fallbacks == [True]
    assert not invalid_plan.plan_mode.endswith("_no_action")


@pytest.mark.parametrize(("addressed", "is_no_action", "trace_result"), [
    pytest.param(_MISSING, False, "rejected", id="missing"),
    pytest.param(None, False, "rejected", id="null"),
    pytest.param(0, False, "rejected", id="zero"),
    pytest.param(1, False, "rejected", id="one"),
    pytest.param("true", False, "rejected", id="string-true"),
    pytest.param([], False, "rejected", id="list"),
    pytest.param({}, False, "rejected", id="object"),
    pytest.param(False, False, "accepted", id="false"),
    pytest.param(True, True, "rejected", id="true"),
])
def test_no_action_requires_exact_json_true_and_preserves_both_attempts(
        monkeypatch, addressed, is_no_action, trace_result):
    agent = _agent("chitchat", "chitchat.talk")

    wire = {"steps": []}
    if addressed is not _MISSING:
        wire["addressed"] = addressed
    raw = json.dumps(wire)
    calls = {"llm": 0, "fallback": 0}

    async def llm(_messages):
        calls["llm"] += 1
        return raw

    async def resolve(_query, top_k=1):
        return []

    async def fallback(_text, _agents):
        calls["fallback"] += 1
        return Plan(steps=[Step(id="fb", agent_id="chitchat",
                                endpoint=agent.endpoint, intent="chitchat.talk")])

    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    builder = planning.PlanBuilder(llm, resolve)
    sink = TraceSink()
    attach_validation_trace(builder, sink)
    builder._fallback = fallback

    # The directive makes addressed=false take the existing retry/fallback
    # safety path too, so every non-True D14 candidate exercises two attempts.
    plan = asyncio.run(builder.build(
        "记住这条信息", WorkingSet(catalog=[agent]), PlanContext(session_id="s")))

    assert calls["llm"] == 2
    assert len(sink.validations) == 2
    assert sink.trace_errors == []
    assert [trace.result for trace in sink.validations] == [trace_result] * 2
    assert [trace.raw_intents for trace in sink.validations] == [(), ()]
    assert [trace.raw_candidate.steps for trace in sink.validations] == [(), ()]
    assert plan.raw_llm == raw
    if is_no_action:
        assert calls["fallback"] == 0
        assert plan.plan_mode.endswith("_no_action")
    else:
        assert calls["fallback"] == 1
        assert not plan.plan_mode.endswith("_no_action")


def test_replan_done_with_invalid_nonempty_ref_is_rejected_not_no_action():
    agent = _agent("alpha", "alpha.one")

    async def llm(_messages):
        return ('{"done":true,"steps":['
                '{"id":"r1","capability_ref":"cap_9999","slots":{}}]}')

    async def resolve(_query, top_k=1):
        return []

    builder = planning.PlanBuilder(llm, resolve)
    sink = TraceSink()
    attach_validation_trace(builder, sink)
    planner = RecordingPlanner(builder, sink)
    decision = asyncio.run(planner.replan(
        "finish safely", [], [agent], PlanContext(session_id="s"),
        working_set=WorkingSet()))

    assert decision.done is True and decision.steps == []
    assert len(sink.validations) == 1
    trace = sink.validations[0]
    assert trace.stage == "replan"
    assert sink.validation_stage == "build"
    assert trace.result == "rejected"
    assert trace.raw_intents == (_INVALID,)
    assert trace.accepted.steps == ()


def test_resolver_preserves_valid_dag_and_validator_rehome_boundary():
    catalog_type = getattr(planning, "PlannerCapabilityCatalog", None)
    assemble = getattr(planning, "_assemble_capability_catalog", None)
    if catalog_type is None or assemble is None:
        pytest.fail(
            "missing referential planner API: "
            "PlannerCapabilityCatalog/_assemble_capability_catalog",
            pytrace=False,
        )
    alpha = _agent("alpha", "alpha.one")
    beta = _agent("beta", "beta.two")
    catalog = assemble([alpha, beta])
    refs = catalog.pair_to_ref
    wire = {
        "addressed": True,
        "complexity": "adaptive",
        "goal": "chain",
        "steps": [
            {"id": "s1", "capability_ref": refs[("alpha", "alpha.one")],
             "slots": {"count": 2}, "depends_on": [], "slot_refs": {}},
            {"id": "s2", "capability_ref": refs[("beta", "beta.two")],
             "slots": {"label": "ok"}, "depends_on": ["s1"],
             "slot_refs": {"value": "s1.data.value"}},
        ],
    }
    builder = planning.PlanBuilder(lambda _messages: None, lambda _query: None)
    plan = builder._parse_and_validate_data(wire, catalog, "fallback")
    assert [(s.agent_id, s.intent) for s in plan.steps] == [
        ("alpha", "alpha.one"), ("beta", "beta.two"),
    ]
    assert plan.steps[0].slots == {"count": "2"}
    assert plan.steps[1].depends_on == ["s1"]
    assert plan.steps[1].slot_refs == {"value": "s1.data.value"}
    assert plan.complexity == "adaptive" and plan.goal == "chain"

    clarify = builder._parse_and_validate_data({
        "addressed": True,
        "steps": [],
        "clarify": {
            "question": "which one?",
            "options": [
                {"label": "first", "send_text": "choose first"},
                {"label": "second", "send_text": "choose second"},
            ],
        },
    }, catalog, "fallback")
    assert clarify.steps == []
    assert clarify.clarify["question"] == "which one?"

    # Reference resolution does not replace the validator's existing unique
    # intent re-home defence for already-resolved internal pairs.
    rehomed = builder._validated_steps([
        {"id": "s1", "agent_id": "alpha", "intent": "beta.two", "slots": {}},
    ], {"alpha": alpha, "beta": beta})
    assert [(s.agent_id, s.intent) for s in rehomed] == [("beta", "beta.two")]
