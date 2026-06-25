"""PlannerEngine T1/T2 dispatch and reactive upgrade."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.protobuf import struct_pb2
from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Plan, PlanContext, Step
from orchestrator.cloud.session import SessionStore
from security.permission import PermissionEngine


class _Planner:
    def __init__(self, plan):
        self.plan = plan

    async def build(self, *_args, **_kwargs):
        return self.plan


class _Clients:
    def __init__(self, responses):
        self.responses = list(responses)
        self.stream_calls = 0
        self.unary_calls = 0

    async def list_agents(self):
        return []

    async def resolve(self, query="", intent="", top_k=1):
        return []

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.unary_calls += 1
        return self.responses.pop(0)

    async def call_agent_stream(self, *args, **kwargs):
        self.stream_calls += 1
        if False:
            yield None


class _LoopSpy:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        yield {"kind": "speech", "delta": "thinking"}
        yield {"kind": "final", "speech": "adaptive done"}


async def _aggregate(_messages, **kwargs):
    return "simple done"


def _response(speech, data=None):
    payload = struct_pb2.Struct()
    payload.update(data or {})
    return agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.OK,
        speech=speech,
        data=payload,
    )


def _request(text="test"):
    return SimpleNamespace(
        text=text, session_id="s1", request_id="r1", is_confirmation=False,
        meta={},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _engine(plan, responses, loop):
    clients = _Clients(responses)
    return PlannerEngine(
        clients=clients,
        planner=_Planner(plan),
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(_aggregate),
        session=SessionStore(redis_url=""),
        perms=PermissionEngine(),
        loop=loop,
    ), clients


def _run(engine):
    async def collect():
        return [event async for event in engine.run(_request())]
    return asyncio.run(collect())


def test_adaptive_plan_uses_loop_and_skips_single_step_stream_fast_path():
    loop = _LoopSpy()
    plan = Plan(
        steps=[Step(id="s1", agent_id="navigation", endpoint="nav:1",
                    intent="navigation.search_poi")],
        complexity="adaptive",
        goal="找到可用充电站",
    )
    engine, clients = _engine(plan, [], loop)

    events = _run(engine)

    assert events[-1]["speech"] == "adaptive done"
    assert len(loop.calls) == 1
    assert loop.calls[0]["initial_plan"] is plan
    assert clients.stream_calls == 0
    assert clients.unary_calls == 0


def test_simple_multi_step_plan_stays_on_existing_t1_path():
    loop = _LoopSpy()
    plan = Plan(steps=[
        Step(id="s1", agent_id="a", endpoint="a:1", intent="a.one"),
        Step(id="s2", agent_id="a", endpoint="a:1", intent="a.two"),
    ])
    engine, clients = _engine(
        plan, [_response("one"), _response("two")], loop)

    events = _run(engine)

    assert events[-1]["speech"] == "simple done"
    assert loop.calls == []
    assert clients.unary_calls == 2


def test_simple_plan_reactively_upgrades_when_result_requests_replan():
    loop = _LoopSpy()
    plan = Plan(
        steps=[
            Step(id="s1", agent_id="a", endpoint="a:1", intent="a.one"),
            Step(id="s2", agent_id="a", endpoint="a:1", intent="a.two"),
        ],
        goal="完成目标",
    )
    engine, _ = _engine(
        plan,
        [_response("one", {"replan": True}), _response("two")],
        loop,
    )

    events = _run(engine)

    assert events[-1]["speech"] == "adaptive done"
    assert len(loop.calls) == 1
    assert loop.calls[0]["initial_plan"] is None
    assert len(loop.calls[0]["seed_results"]) == 2


def test_poc_default_scopes_used_when_granted_scopes_missing():
    """When HandleRequest.meta has no granted_scopes, PoC defaults are injected."""
    plan = Plan(
        steps=[Step(id="s1", agent_id="navigation", endpoint="nav:1",
                    intent="navigation.search_poi",
                    required_permissions=["navigation"])],
        complexity="simple",
    )
    clients = _Clients([_response("found")])
    engine = PlannerEngine(
        clients=clients,
        planner=_Planner(plan),
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(_aggregate),
        session=SessionStore(redis_url=""),
        perms=PermissionEngine(),
    )

    # Request with no granted_scopes in meta
    req = SimpleNamespace(
        text="找充电站", session_id="s1", request_id="r1",
        is_confirmation=False, meta={},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )

    async def collect():
        return [event async for event in engine.run(req)]
    events = asyncio.run(collect())

    # Should succeed — PoC defaults include "navigation"
    assert events[-1]["speech"] == "found"


def test_explicit_granted_scopes_passed_to_planner():
    """When granted_scopes is explicitly provided, it flows to the planner."""
    captured_permissions = []

    class _CapturingPlanner:
        def __init__(self, plan):
            self.plan = plan

        async def build(self, text, agents, ctx, granted_permissions=None,
                        history=None, memory=None):
            captured_permissions.append(list(granted_permissions or []))
            return self.plan

    plan = Plan(
        steps=[Step(id="s1", agent_id="navigation", endpoint="nav:1",
                    intent="navigation.search_poi")],
        complexity="simple",
    )
    clients = _Clients([_response("found")])
    engine = PlannerEngine(
        clients=clients,
        planner=_CapturingPlanner(plan),
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(_aggregate),
        session=SessionStore(redis_url=""),
        perms=PermissionEngine(),
    )

    req = SimpleNamespace(
        text="找充电站", session_id="s1", request_id="r1",
        is_confirmation=False, meta={"granted_scopes": "vehicle.control"},
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )

    async def collect():
        return [event async for event in engine.run(req)]
    asyncio.run(collect())

    # Explicit scopes should be passed through, not replaced with PoC defaults
    assert captured_permissions[0] == ["vehicle.control"]
