"""CA2-08 caller side: server-owned operation identity on durable steps.

The identity must survive suspension (so a confirmed re-dispatch reaches the same
executor record), reach every cloud exit, and never come from a client, a model
or a parent agent. Non-durable steps stay exactly as before.
"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from cockpit.agent.v1 import agent_pb2
from runtime import capability_contract as cc
from runtime import operation as op
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.clients import Clients
from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import PlanContext, Step, StepStatus, step_record
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore


def durable_capability(*, require_confirm=True, admission="durable"):
    contract = cc.declaration(["item"], "external_write", legacy=False, admission=admission,
                              preconditions=("permission", "handler", "confirmation"),
                              parameters={"item": {"type": "string"}})
    cap = agent_pb2.Capability(intent="sample.order", description="sample.order", effect="write",
                               slots=["item"], require_confirm=require_confirm,
                               contract=cc.to_proto(contract))
    manifest = agent_pb2.AgentManifest(
        agent_id="sample", kind="agent", deployment="cloud", trust_level="first_party",
        requires_permissions=["sample.write"], latency_budget_ms=2000, capabilities=[cap])
    return manifest, manifest.capabilities[0]


def durable_step(step_id="s1", **over):
    manifest, cap = durable_capability()
    return Step(step_id, "sample", endpoint="stub:1", intent=cap.intent, slots={"item": "latte"},
                **cc.step_fields(manifest, cap), **over)


def header_of(meta) -> op.OperationRef:
    return op.decode_ref(meta[op.HEADER])


def test_durable_step_gets_a_server_identity_that_survives_suspension():
    step = durable_step()
    assert op.valid_operation_id(step.operation_id)
    ref = header_of(step.meta)
    assert (ref.operation_id, ref.step_id) == (step.operation_id, "s1")
    step.meta["confirmed"] = "true"
    record = step_record(step)
    assert record["operation_id"] == step.operation_id
    restored = Step(**json.loads(json.dumps(record)))
    assert restored.operation_id == step.operation_id
    assert header_of(restored.meta).operation_id == step.operation_id
    assert "confirmed" not in restored.meta


def test_new_steps_are_new_operations():
    first, second = durable_step(), durable_step()
    assert first.operation_id != second.operation_id


def test_non_durable_steps_never_carry_an_operation():
    manifest, cap = durable_capability(admission=None)
    plain = Step("s1", "sample", intent=cap.intent, **cc.step_fields(manifest, cap))
    legacy = Step("s2", "nearby", intent="nearby.search")
    for step in (plain, legacy):
        assert step.operation_id == "" and op.HEADER not in step.meta
        assert "operation_id" not in step_record(step)


@pytest.mark.parametrize("mutate,reason", [
    (lambda r: r.update(operation_id="not-a-uuid"), "corrupt_step_operation"),
    (lambda r: r.pop("capability_contract"), "incomplete"),
])
def test_corrupt_operation_records_do_not_restore(mutate, reason):
    record = step_record(durable_step())
    mutate(record)
    with pytest.raises(cc.ContractError, match=reason):
        Step(**record)


def test_an_operation_without_a_durable_declaration_is_corrupt():
    manifest, cap = durable_capability(admission=None)
    record = step_record(Step("s1", "sample", intent=cap.intent, **cc.step_fields(manifest, cap)))
    record["operation_id"] = op.new_operation_id()
    with pytest.raises(cc.ContractError, match="operation_without_admission"):
        Step(**record)


def test_model_output_cannot_choose_the_operation():
    manifest, cap = durable_capability()
    forged = op.new_operation_id()
    steps = PlanBuilder._validated_steps([{
        "id": "s1", "agent_id": "sample", "intent": cap.intent, "slots": {"item": "latte"},
        "operation_id": forged, "meta": {op.HEADER: op.encode_ref(op.OperationRef(forged))},
    }], {"sample": SimpleNamespace(manifest=manifest, endpoint="stub")})
    assert len(steps) == 1
    assert op.valid_operation_id(steps[0].operation_id) and steps[0].operation_id != forged
    assert header_of(steps[0].meta).operation_id == steps[0].operation_id


def test_clients_strip_forged_headers_and_add_plan_correlation():
    forged = op.encode_ref(op.OperationRef(op.new_operation_id()))
    ctx = PlanContext(user_id="u1", prefs={op.HEADER: forged},
                      task_identity={"task_id": "task-1", "plan_revision": 3})
    assert op.HEADER not in Clients._merge_meta(ctx, None)
    step = durable_step()
    merged = Clients._merge_meta(ctx, step.meta)
    ref = header_of(merged)
    assert (ref.operation_id, ref.step_id, ref.task_id, ref.plan_revision) == (
        step.operation_id, "s1", "task-1", 3)


def test_unary_dispatch_sends_the_step_identity():
    seen = []

    async def cloud_call(endpoint, intent, slots, ctx, meta, timeout=None, context_scopes=None):
        seen.append(dict(meta))
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="ok")

    step = durable_step()
    dispatcher = UnifiedDispatcher(cloud_call, edge_call=None)
    ctx = PlanContext(user_id="u1", granted_permissions=["sample.write"])
    asyncio.run(dispatcher.dispatch(step, ctx))
    assert header_of(seen[0]).operation_id == step.operation_id


def test_stream_dispatch_sends_the_step_identity():
    seen = []

    class _Clients:
        async def call_agent_stream(self, endpoint, intent, slots, ctx, meta, timeout=None,
                                    context_scopes=None):
            seen.append(dict(meta))
            yield ("final", agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK,
                                                      speech="ok"))

    async def unused(*_a, **_k):  # pragma: no cover
        raise AssertionError("stream path must not fall back to unary")

    engine = PlannerEngine(clients=_Clients(), planner=None,
                           executor=DagExecutor(call_agent_fn=unused),
                           aggregator=None, session=SessionStore(redis_url=""))
    step = durable_step(require_confirm=False, kind="agent", deployment="cloud")

    async def run():
        return [e async for e in engine._stream_single_step(step, PlanContext(trace_id="t"), False, {})]

    asyncio.run(run())
    assert header_of(seen[0]).operation_id == step.operation_id


class _ConfirmSpy:
    """First dispatch asks for confirmation; the confirmed one completes."""

    def __init__(self):
        self.calls: list[dict] = []
        manifest, _ = durable_capability()
        self.agent = SimpleNamespace(manifest=manifest, endpoint="stub:1")

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append(dict(meta or {}))
        if (meta or {}).get("confirmed") == "true":
            return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="已下单。")
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.NEED_CONFIRM,
                                         speech="确认下单一杯拿铁吗？")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return json.dumps({"steps": [{"id": "s1", "capability_ref": "cap_0001",
                                          "slots": {"item": "拿铁"}, "depends_on": [],
                                          "slot_refs": {}}]})
        return "（聚合）"

    async def resolve(self, query="", intent="", top_k=1):
        return [self.agent]

    async def list_agents(self):
        return [self.agent]


def _turn(engine, text, *, confirm=False, operation_id=""):
    req = SimpleNamespace(text=text, session_id="sess-op", request_id="r-" + text,
                          is_confirmation=confirm, operation_id=operation_id,
                          context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
                          meta={"granted_scopes": "sample.write"})

    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_confirmation_resumes_the_same_operation():
    spy = _ConfirmSpy()
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=SessionStore(redis_url=""))
    first = _turn(engine, "帮我下单一杯拿铁")
    pending = [e for e in first if e.get("kind") == "final"][-1]
    assert pending.get("need_confirm") is True and pending.get("operation_id")
    _turn(engine, "确认", confirm=True, operation_id=pending["operation_id"])
    assert len(spy.calls) == 2 and spy.calls[1].get("confirmed") == "true"
    original, confirmed = (header_of(m).operation_id for m in spy.calls)
    assert op.valid_operation_id(original) and original == confirmed
    # The client-facing pending address is a different key.
    assert pending["operation_id"] != original


def test_replanned_batches_are_new_operations():
    first = durable_step("s1")
    replanned = Step(**{k: v for k, v in step_record(first).items() if k != "operation_id"})
    assert replanned.operation_id != first.operation_id
    assert copy.deepcopy(first).operation_id == first.operation_id
