"""Request-local capability reference contract for the cloud planner.

These tests intentionally describe the target wire before its implementation.  Keep
the fixtures small: the existing planning suite owns legacy behaviour coverage.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace

import pytest

from orchestrator.cloud import context, exemplars, planning, skills
from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import Plan, PlanContext, Step


def _agent(agent_id: str, *intents: str, description: str = "",
           protected: bool = False, permissions: tuple[str, ...] = ()):
    caps = [SimpleNamespace(intent=intent, slots=[], description=description,
                            examples=[], heavy=False, require_confirm=False,
                            verification=None)
            for intent in intents]
    manifest = SimpleNamespace(
        agent_id=agent_id,
        capabilities=caps,
        latency_budget_ms=5000,
        kind="edge_fast" if protected else "agent",
        deployment="edge" if protected else "cloud",
        requires_permissions=list(permissions),
        trust_level="first_party",
        context_scopes=[],
        route_hints=[],
        category="core" if protected else "",
    )
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50051")


def _ref_api():
    """Resolve planned APIs at test time so missing symbols are test failures, not
    collection errors.
    """
    catalog_type = getattr(planning, "PlannerCapabilityCatalog", None)
    assemble = getattr(planning, "_assemble_capability_catalog", None)
    if catalog_type is None or assemble is None:
        pytest.fail(
            "missing referential planner API: "
            "PlannerCapabilityCatalog/_assemble_capability_catalog",
            pytrace=False,
        )
    return catalog_type, assemble


def _pairs(catalog) -> set[tuple[str, str]]:
    return set(catalog.ref_to_pair.values())


def _step_schema(tool_spec: dict) -> dict:
    return (tool_spec["tools"][0]["function"]["parameters"]["properties"]
            ["steps"]["items"])


def _legacy_step_shape(text: str) -> bool:
    return bool(re.search(r'["\'](?:agent_id|intent)["\']\s*:', text))


def test_request_catalog_is_opaque_immutable_and_budgeted_as_rendered_text(monkeypatch):
    catalog_type, assemble = _ref_api()
    agents = [
        _agent("beta", "beta.two", description="B" * 80),
        _agent("alpha", "alpha.one", "alpha.extra", "alpha.one",
               description="A" * 80),
    ]
    monkeypatch.setattr(context, "_CATALOG_BUDGET", 100_000)

    first = assemble(agents)
    second = assemble(list(reversed(agents)))

    assert isinstance(first, catalog_type)
    assert dict(first.ref_to_pair) == {
        "cap_0001": ("alpha", "alpha.extra"),
        "cap_0002": ("alpha", "alpha.one"),
        "cap_0003": ("beta", "beta.two"),
    }
    assert dict(first.ref_to_pair) == dict(second.ref_to_pair)
    assert first is not second and first.ref_to_pair is not second.ref_to_pair
    assert all(re.fullmatch(r"cap_\d{4}", ref) for ref in first.ref_to_pair)
    assert all(not any(token in ref for token in ("alpha", "beta", "one", "two"))
               for ref in first.ref_to_pair)
    assert dict(first.pair_to_ref) == {pair: ref for ref, pair in first.ref_to_pair.items()}
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        first.semantic_mapping_text = "changed"
    with pytest.raises((AttributeError, TypeError)):
        first.ref_to_pair["cap_9999"] = ("x", "x.y")

    full_chars = len(first.semantic_mapping_text)
    monkeypatch.setattr(context, "_CATALOG_BUDGET", full_chars - 1)
    cropped = assemble(agents)
    assert cropped.catalog_stats == {
        "chars_full": full_chars,
        "chars_final": len(cropped.semantic_mapping_text),
        "dropped": cropped.catalog_stats["dropped"],
    }
    assert cropped.catalog_stats["dropped"]
    visible_ids = {a.manifest.agent_id for a in cropped.visible_agents}
    expected_pairs = {
        (a.manifest.agent_id, cap.intent)
        for a in cropped.visible_agents for cap in a.manifest.capabilities
    }
    assert _pairs(cropped) == expected_pairs
    assert set(cropped.agent_map) == visible_ids
    assert set(cropped.pair_to_ref) == expected_pairs
    assert all(aid in visible_ids for aid, _ in _pairs(cropped))

    # Existing stop semantics remain fail-open for availability: protected-only
    # and a single oversized non-protected catalog are never truncated to empty.
    monkeypatch.setattr(context, "_CATALOG_BUDGET", 1)
    protected = assemble([
        _agent("protected-a", "a.one", description="x" * 100, protected=True),
        _agent("protected-b", "b.one", description="y" * 100, protected=True),
    ])
    singleton = assemble([_agent("last", "last.one", description="z" * 100)])
    assert len(protected.visible_agents) == 2
    assert len(singleton.visible_agents) == 1
    assert protected.catalog_stats["chars_final"] > 1
    assert singleton.catalog_stats["chars_final"] > 1
    assert protected.catalog_stats["dropped"] == []
    assert singleton.catalog_stats["dropped"] == []


def test_ref_only_schema_static_prompts_and_user_message_tail(monkeypatch):
    _, assemble = _ref_api()
    monkeypatch.setattr(context, "_CATALOG_BUDGET", 100_000)
    catalog = assemble([_agent("alpha", "alpha.one"), _agent("beta", "beta.two")])

    item = _step_schema(planning._submit_plan_tools(catalog))
    assert set(item["properties"]) == {
        "id", "capability_ref", "slots", "depends_on", "slot_refs",
    }
    assert {"id", "capability_ref"} <= set(item["required"])
    assert item["properties"]["capability_ref"]["enum"] == list(catalog.ref_to_pair)
    assert not _legacy_step_shape(json.dumps(item, ensure_ascii=False))
    empty_steps = (planning._submit_plan_tools(assemble([]))["tools"][0]["function"]
                   ["parameters"]["properties"]["steps"])
    assert empty_steps["maxItems"] == 0

    for static_prompt in (
        planning._PLANNER_BASE,
        planning._CATALOG_ALLOWLIST_SECTION,
        planning._REPLAN_SYSTEM,
        planning._planner_system(),
        planning._planner_system(toolcall=True),
    ):
        assert not _legacy_step_shape(static_prompt)
    assert "capability_ref" in planning._PLANNER_BASE
    assert "capability_ref" in planning._REPLAN_SYSTEM

    ws = WorkingSet(history=[{"role": "user", "text": "history-marker"}])
    message = planning.PlanBuilder._planner_user_msg(
        "utterance-marker", catalog, ws,
        skills_block="skill-marker", exemplars_block="exemplar-marker",
    )
    assert message.count(catalog.semantic_mapping_text) == 1
    positions = [message.index(marker) for marker in (
        "skill-marker", "exemplar-marker", "history-marker",
        catalog.semantic_mapping_text, "utterance-marker",
    )]
    assert positions == sorted(positions)
    assert message.rstrip().endswith("utterance-marker")
    assert _pairs(catalog) == set(catalog.pair_to_ref) == {
        (aid, cap.intent)
        for aid, agent in catalog.agent_map.items()
        for cap in agent.manifest.capabilities
    }


def test_build_retry_and_replan_use_one_catalog_and_one_parse_seam(monkeypatch):
    _, assemble = _ref_api()
    alpha = _agent("alpha", "alpha.one")
    denied = _agent("denied", "denied.one", permissions=("secret.use",))
    agents = [alpha, denied]
    catalog = assemble([alpha])
    assembled: list[object] = []

    def assemble_spy(candidates):
        assembled.append(tuple(candidates))
        return catalog

    monkeypatch.setattr(planning, "_assemble_capability_catalog", assemble_spy)

    async def no_skills(_text):
        return "off", [], ""

    async def no_exemplars(_text):
        return "off", [], ""

    monkeypatch.setattr(skills, "plan_skills", no_skills)
    monkeypatch.setattr(exemplars, "plan_exemplars", no_exemplars)
    monkeypatch.setenv("PLANNER_TOOLCALL", "on")
    repaired: list[list[tuple[str, str]]] = []

    def repair_spy(plan, _text, _names):
        repaired.append([(step.agent_id, step.intent) for step in plan.steps])
        return ["ref-contract-repair"]

    monkeypatch.setattr(skills, "apply_plan_repairs", repair_spy)
    valid_wire = {
        "addressed": True,
        "steps": [{"id": "s1", "capability_ref": "cap_0001", "slots": {},
                   "depends_on": [], "slot_refs": {}}],
    }

    async def llm(_messages):
        return json.dumps(valid_wire)

    async def llm_tools(_messages, _tools):
        # First round refuses the tool. Its text is salvaged through the seam,
        # then the second JSON round uses the same catalog object.
        return json.dumps({"addressed": True,
                           "steps": [{"id": "bad", "capability_ref": "missing"}]}), []

    async def resolve(_query, top_k=1):
        return []

    builder = planning.PlanBuilder(llm, resolve, llm_tools)
    seen: list[object] = []

    def parse_spy(wire, seen_catalog, fallback_text):
        seen.append(seen_catalog)
        if wire == valid_wire:
            return Plan(steps=[Step(id="s1", agent_id="alpha",
                                    endpoint="alpha:50051", intent="alpha.one")],
                        raw_text=fallback_text)
        return None

    builder._parse_and_validate_data = parse_spy
    plan = asyncio.run(builder.build(
        "do alpha", WorkingSet(catalog=agents), PlanContext(session_id="s"),
        granted_permissions=[]))
    assert len(assembled) == 1
    assert [a.manifest.agent_id for a in assembled[0]] == ["alpha"]
    assert seen == [catalog, catalog]
    assert plan.steps[0].intent == "alpha.one"
    assert repaired == [[("alpha", "alpha.one")]]
    assert plan.skill_effects == ["ref-contract-repair"]

    # Replan owns a fresh request catalog and even done=true/empty steps passes
    # through the same seam before the decision is returned.
    assembled.clear()
    seen.clear()

    async def done_llm(_messages):
        return '{"done":true,"steps":[]}'

    builder._llm = done_llm
    decision = asyncio.run(builder.replan(
        "done", [], agents, PlanContext(session_id="s"), working_set=WorkingSet()))
    assert decision.done is True
    assert len(assembled) == 1
    assert seen == [catalog]

    signature = inspect.signature(planning.PlanBuilder._parse_and_validate_data)
    assert list(signature.parameters)[:4] == ["self", "wire", "catalog", "fallback_text"]


def test_dynamic_skill_and_exemplar_render_refs_and_drop_partial_dags(monkeypatch):
    _, assemble = _ref_api()
    catalog = assemble([_agent("alpha", "alpha.one"), _agent("beta", "beta.two")])
    refs = catalog.pair_to_ref
    two_steps = [
        {"id": "s1", "agent_id": "alpha", "intent": "alpha.one", "slots": {}},
        {"id": "s2", "agent_id": "beta", "intent": "beta.two", "slots": {},
         "depends_on": ["s1"], "slot_refs": {"value": "s1.data.value"}},
    ]
    skill = skills.SkillDoc(
        name="ref-contract", type="guide", description="test", knowledge="knowledge",
        body="knowledge", few_shots=({"user": "skill-example", "plan": {"steps": two_steps}},),
    )
    exemplar_steps = (
        {"id": "s1", "agent": "alpha", "intent": "alpha.one", "slots": {}},
        {"id": "s2", "agent": "beta", "intent": "beta.two", "slots": {},
         "depends_on": ["s1"], "slot_refs": {"value": "s1.data.value"}},
    )
    exemplar = exemplars.Exemplar(
        eid="test#1", domain="test", text="exemplar-example",
        plan=exemplar_steps, source="manual",
    )

    skill_block, _, _ = skills.render_skills_block(
        [], [skill], capability_refs=refs, budget=10_000)
    exemplar_block, _, _ = exemplars.render_block(
        [exemplar], capability_refs=refs, budget=10_000)
    for block in (skill_block, exemplar_block):
        assert "cap_0001" in block and "cap_0002" in block
        assert not _legacy_step_shape(block)

    only_alpha = MappingProxyType({("alpha", "alpha.one"): "cap_0001"})
    partial_skill, _, _ = skills.render_skills_block(
        [], [skill], capability_refs=only_alpha, budget=10_000)
    partial_exemplar, injected, _ = exemplars.render_block(
        [exemplar], capability_refs=only_alpha, budget=10_000)
    assert "skill-example" not in partial_skill
    assert "exemplar-example" not in partial_exemplar
    assert injected == []

    monkeypatch.setattr(skills, "default_store", lambda: SimpleNamespace(load=lambda: [skill]))
    monkeypatch.setattr(exemplars, "default_store",
                        lambda: SimpleNamespace(load=lambda: [exemplar]))
    assert "cap_0001" in skills.render_for_names(
        ["full:ref-contract@lex:10"], capability_refs=refs)
    assert "cap_0001" in exemplars.render_for_names(
        ["full:test#1@lex:1.00"], capability_refs=refs)
