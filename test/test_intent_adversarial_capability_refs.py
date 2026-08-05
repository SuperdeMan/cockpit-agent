"""Raw-evidence and validator boundaries for planner capability references."""
from __future__ import annotations

import asyncio
import sys
from types import MappingProxyType, SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.cloud import planning  # noqa: E402
from orchestrator.cloud.context import WorkingSet  # noqa: E402
from orchestrator.cloud.models import Plan, PlanContext, Step  # noqa: E402
from support.intent_adversarial_trace import (  # noqa: E402
    TraceSink,
    attach_validation_trace,
)


_INVALID = "__invalid_capability_reference__"


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
