"""Regression coverage for slow-intent completeness and PoC permissions."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from orchestrator.cloud.engine import _POC_DEFAULT_SCOPES
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.context import WorkingSet


def _agent(agent_id: str, intents: list[str], permissions: list[str] | None = None):
    capabilities = [
        SimpleNamespace(intent=intent, slots=[], description="", examples=[])
        for intent in intents
    ]
    manifest = SimpleNamespace(
        agent_id=agent_id,
        capabilities=capabilities,
        latency_budget_ms=5000,
        kind="agent",
        deployment="cloud",
        requires_permissions=permissions or [],
        trust_level="first_party",
    )
    return SimpleNamespace(manifest=manifest, endpoint=f"{agent_id}:50000")


async def _no_resolve(query: str, top_k: int = 1):
    return []


def test_chitchat_step_always_receives_current_user_text():
    agents = [_agent("chitchat", ["chitchat.talk"])]

    async def llm(messages):
        return (
            '{"steps":[{"id":"s1","capability_ref":"cap_0001",'
            '"slots":{"text":"stale text"}}]}'
        )

    text = "给我讲个笑话。"
    builder = PlanBuilder(llm, _no_resolve)
    fallback_calls = []

    async def forbidden_fallback(*_args, **_kwargs):
        fallback_calls.append(True)
        raise AssertionError("valid capability ref must not use fallback")

    builder._fallback = forbidden_fallback
    plan = asyncio.run(builder.build(
        text, WorkingSet(catalog=agents), PlanContext()))

    assert fallback_calls == []
    assert len(plan.steps) == 1
    assert plan.steps[0].slots["text"] == text


def test_partial_invalid_plan_is_retried_atomically():
    agents = [_agent("chitchat", ["chitchat.talk"])]
    replies = iter([
        (
            '{"steps":['
            '{"id":"s1","capability_ref":"cap_0001","slots":{}},'
            '{"id":"s2","capability_ref":"cap_9999","slots":{}}'
            ']}'
        ),
        (
            '{"steps":[{"id":"s1","capability_ref":"cap_0001","slots":{}}]}'
        ),
    ])
    calls = 0

    async def llm(messages):
        nonlocal calls
        calls += 1
        return next(replies)

    text = "给我讲个笑话吧，顺便说说北京那边天气怎么样。"
    builder = PlanBuilder(llm, _no_resolve)
    fallback_calls = []

    async def forbidden_fallback(*_args, **_kwargs):
        fallback_calls.append(True)
        raise AssertionError("atomic retry must not use fallback")

    builder._fallback = forbidden_fallback
    plan = asyncio.run(builder.build(
        text, WorkingSet(catalog=agents), PlanContext()))

    assert calls == 2
    assert fallback_calls == []
    assert len(plan.steps) == 1
    assert plan.steps[0].agent_id == "chitchat"
    assert plan.steps[0].slots["text"] == text


def test_poc_default_scopes_cover_current_cloud_agents():
    assert {
        "location.read",
        "navigation.control",
        "network.external",
        "payment.invoke",
    }.issubset(set(_POC_DEFAULT_SCOPES))
