"""CA2-09: a pending is consumed once, and a confirmation authorizes only what was confirmed.

Concurrency runs two real engine turns against one in-memory SessionStore. The
agent stub is slow on the consuming call so the turns overlap; exactly one of
them may dispatch. The loser gets a zero-action answer and leaves the pending
to the winner. Binding refusals must happen before any dispatch.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from cockpit.agent.v1 import agent_pb2
from runtime import capability_contract as cc
from orchestrator.cloud import confirmation
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import PlanContext, SessionState, Step, StepStatus, step_record
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import (CLAIM_ABSENT, CLAIM_FENCED, CLAIM_OK, CLAIM_TAKEN,
                                        SessionStore)


# ── store ──────────────────────────────────────────────────────────────────

def _state(op="op-1", **over):
    base = dict(phase="wait_confirm", owner_user_id="u1", operation_id=op,
                pending_step_id="s1", pending_plan={"steps": []})
    base.update(over)
    return SessionState(**base)


def test_a_pending_can_be_claimed_exactly_once():
    store = SessionStore(redis_url="")

    async def run():
        await store.save("s", _state())
        first = await store.claim_result("s", owner_user_id="u1", operation_id="op-1", token="a")
        second = await store.claim_result("s", owner_user_id="u1", operation_id="op-1", token="b")
        stored = await store.load("s", owner_user_id="u1", operation_id="op-1")
        return first, second, stored

    first, second, stored = asyncio.run(run())
    assert first[0] == CLAIM_OK and second[0] == CLAIM_TAKEN
    assert second[1].claimed_by == "a" and stored.claimed_by == "a" and stored.claimed_at > 0


def test_claims_respect_owner_expiry_address_and_privacy_fence():
    store = SessionStore(redis_url="")

    async def run():
        await store.save("s", _state())
        await store.save("s", _state("op-old", expires_at=time.time() - 1))
        other = await store.claim_result("s", owner_user_id="u2", operation_id="op-1", token="x")
        gone = await store.claim_result("s", owner_user_id="u1", operation_id="op-9", token="x")
        expired = await store.claim_result("s", owner_user_id="u1", operation_id="op-old", token="x")
        store._owner_fences["u1"] = time.time() + 60
        fenced = await store.claim_result("s", owner_user_id="u1", operation_id="op-1", token="x")
        return other[0], gone[0], expired[0], fenced[0]

    assert asyncio.run(run()) == (CLAIM_ABSENT, CLAIM_ABSENT, CLAIM_ABSENT, CLAIM_FENCED)


def test_a_claimed_pending_is_still_removed_by_its_settlement():
    store = SessionStore(redis_url="")

    async def run():
        await store.save("s", _state())
        await store.claim_result("s", owner_user_id="u1", operation_id="op-1", token="a")
        await store.clear("s", owner_user_id="u1", operation_id="op-1")
        return await store.load_all("s", owner_user_id="u1")

    assert asyncio.run(run()) == []


# ── engine: one consumer per pending ───────────────────────────────────────

def _manifest(require_confirm=True):
    contract = cc.declaration(["item"], "external_write", legacy=False,
                              preconditions=("permission", "handler", "confirmation"),
                              parameters={"item": {"type": "string"}})
    cap = agent_pb2.Capability(intent="sample.order", description="sample.order", effect="write",
                               slots=["item"], require_confirm=require_confirm,
                               contract=cc.to_proto(contract))
    return agent_pb2.AgentManifest(agent_id="sample", kind="agent", deployment="cloud",
                                   trust_level="first_party", requires_permissions=["sample.write"],
                                   latency_budget_ms=2000, capabilities=[cap])


class _Spy:
    """First call asks to confirm (or for a slot); the consuming call is slow so turns overlap."""

    def __init__(self, *, ask="confirm"):
        self.ask = ask
        self.calls: list[dict] = []
        self.agent = SimpleNamespace(manifest=_manifest(require_confirm=ask == "confirm"),
                                     endpoint="stub:1")

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append({"meta": dict(meta or {}), "slots": dict(slots or {})})
        if self.ask == "confirm":
            if (meta or {}).get("confirmed") == "true":
                await asyncio.sleep(0.2)
                return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="已下单。")
            return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.NEED_CONFIRM,
                                             speech="确认下单吗？")
        if len(self.calls) == 1:
            return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.NEED_SLOT,
                                             speech="要哪一款？", missing_slots=["item"])
        await asyncio.sleep(0.2)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="已下单。")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return json.dumps({"steps": [{"id": "s1", "capability_ref": "cap_0001",
                                          "slots": {"item": "拿铁"} if self.ask == "confirm" else {},
                                          "depends_on": [], "slot_refs": {}}]})
        return "（聚合）"

    async def resolve(self, query="", intent="", top_k=1):
        return [self.agent]

    async def list_agents(self):
        return [self.agent]


class _YieldingStore(SessionStore):
    """Reading the pending table yields like a network read, so concurrent turns both see it."""

    async def load_all_result(self, *args, **kwargs):
        result = await super().load_all_result(*args, **kwargs)
        await asyncio.sleep(0.05)
        return result


def _engine(spy):
    return PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                         executor=DagExecutor(call_agent_fn=spy.call_agent),
                         aggregator=Aggregator(llm_fn=spy.llm), session=_YieldingStore(redis_url=""))


def _req(text, *, confirm=False, operation_id="", vehicle="v1", rid="r"):
    return SimpleNamespace(text=text, session_id="sess", request_id=rid + text,
                           is_confirmation=confirm, operation_id=operation_id,
                           context=SimpleNamespace(user_id="u1", vehicle_id=vehicle),
                           meta={"granted_scopes": "sample.write"})


async def _collect(engine, req):
    return [e async for e in engine.run(req)]


def _final(events):
    return [e for e in events if e.get("kind") == "final"][-1]


def test_concurrent_confirmations_dispatch_once():
    spy = _Spy()
    engine = _engine(spy)

    async def run():
        first = _final(await _collect(engine, _req("帮我下单")))
        op = first["operation_id"]
        a, b = await asyncio.gather(
            _collect(engine, _req("确认", confirm=True, operation_id=op, rid="a")),
            _collect(engine, _req("确认", confirm=True, operation_id=op, rid="b")))
        return op, _final(a), _final(b)

    op, a, b = asyncio.run(run())
    confirmed = [c for c in spy.calls if c["meta"].get("confirmed") == "true"]
    assert len(confirmed) == 1
    claimed = "这条操作已经在处理了，我没有重复执行。"
    assert sorted(x["speech"] for x in (a, b)) == sorted([claimed, "已下单。"])
    loser = a if a["speech"] == claimed else b
    assert not loser.get("actions") and op not in (loser.get("closed_operation_ids") or [])


def test_concurrent_slot_answers_dispatch_once():
    spy = _Spy(ask="slot")
    engine = _engine(spy)

    async def run():
        first = _final(await _collect(engine, _req("帮我下单")))
        op = first["operation_id"]
        await asyncio.gather(_collect(engine, _req("拿铁", operation_id=op, rid="a")),
                             _collect(engine, _req("拿铁", operation_id=op, rid="b")))

    asyncio.run(run())
    assert len(spy.calls) == 2   # the question plus exactly one resumed dispatch


def test_cancel_racing_a_confirmation_never_claims_success_it_cannot_prove():
    spy = _Spy()
    engine = _engine(spy)

    async def run():
        op = _final(await _collect(engine, _req("帮我下单")))["operation_id"]
        confirm_task = asyncio.ensure_future(
            _collect(engine, _req("确认", confirm=True, operation_id=op, rid="a")))
        await asyncio.sleep(0.05)
        cancelled = _final(await _collect(engine, _req("取消", operation_id=op, rid="b")))
        return cancelled, _final(await confirm_task)

    cancelled, confirmed = asyncio.run(run())
    assert cancelled["speech"] == "这条操作已经在处理了，没法再撤回；稍后可以问我结果。"
    assert confirmed.get("speech") == "已下单。"
    assert len([c for c in spy.calls if c["meta"].get("confirmed") == "true"]) == 1


# ── binding ────────────────────────────────────────────────────────────────

def test_confirmation_from_another_vehicle_is_refused_before_dispatch():
    spy = _Spy()
    engine = _engine(spy)

    async def run():
        op = _final(await _collect(engine, _req("帮我下单")))["operation_id"]
        state = await engine.session.load("sess", owner_user_id="u1", operation_id=op)
        result = _final(await _collect(
            engine, _req("确认", confirm=True, operation_id=op, vehicle="v2", rid="a")))
        return state, result

    state, result = asyncio.run(run())
    assert state.confirmation["vehicle_id"] == "v1" and state.confirmation["params_sha256"]
    assert not [c for c in spy.calls if c["meta"].get("confirmed") == "true"]
    assert result["speech"] == confirmation.SPEECH_MISMATCH


def test_slot_suspensions_carry_no_confirmation_binding():
    spy = _Spy(ask="slot")
    engine = _engine(spy)

    async def run():
        op = _final(await _collect(engine, _req("帮我下单")))["operation_id"]
        return await engine.session.load("sess", owner_user_id="u1", operation_id=op)

    assert asyncio.run(run()).confirmation == {}


def _step(**over):
    manifest = _manifest()
    step = Step("s1", "sample", intent="sample.order", slots={"item": "拿铁"},
                **cc.step_fields(manifest, manifest.capabilities[0]))
    for key, value in over.items():
        setattr(step, key, value)
    return step


def test_binding_checks_vehicle_parameters_revision_operation_and_seal():
    step = _step()
    recorded = confirmation.binding(step, vehicle_id="v1", task_identity={"task_id": "t", "plan_revision": 2},
                                    edge_state={"driving": False, "gear": "P"})
    assert confirmation.mismatch(step, recorded, vehicle_id="v1") == ""
    assert confirmation.mismatch(step, recorded, vehicle_id="v2") == confirmation.MISMATCH
    assert confirmation.mismatch(_step(slots={"item": "摩卡"}), recorded, vehicle_id="v1")
    assert confirmation.mismatch(_step(capability_revision="0" * 64), recorded, vehicle_id="v1")
    assert confirmation.mismatch(_step(operation_id=""), {**recorded}, vehicle_id="v1") == ""
    tampered = {**recorded, "edge_state": {"driving": True, "gear": "D"}}
    assert confirmation.mismatch(step, tampered, vehicle_id="v1") == confirmation.MISMATCH
    assert confirmation.mismatch(step, {}, vehicle_id="v9") == ""          # pre-CA2-09 record
    assert confirmation.mismatch(step, {"v": 2}, vehicle_id="v1") == confirmation.MISMATCH
    assert json.loads(confirmation.edge_state_meta(recorded)) == {"driving": False, "gear": "P"}


def test_executor_refuses_a_confirmed_step_whose_final_parameters_changed():
    calls = []

    async def call_agent(endpoint, intent, slots, ctx, meta):
        calls.append(slots)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="ok")

    step = _step()
    step.meta["confirmed"] = "true"
    step.confirmation_binding = confirmation.binding(step, vehicle_id="v1")
    step.slots["item"] = "摩卡"                       # e.g. a slot ref re-resolved differently
    result = asyncio.run(DagExecutor(call_agent_fn=call_agent)._exec_step(
        step, {}, PlanContext(user_id="u1", vehicle_id="v1")))
    assert calls == [] and result.status == StepStatus.FAILED
    assert result.error == confirmation.MISMATCH


def test_restore_carries_the_binding_and_the_edge_snapshot_only_to_the_confirmed_step():
    step = _step()
    other = Step("s2", "sample", intent="sample.order")
    binding = confirmation.binding(step, vehicle_id="v1", edge_state={"driving": False, "gear": "P"})
    state = _state(pending_plan={"steps": [step_record(step), step_record(other)]},
                   confirmation=binding)
    plan, _seeds = PlannerEngine._restore(None, state, inject_confirmed=True)
    restored = {s.id: s for s in plan.steps}
    assert restored["s1"].confirmation_binding == binding
    assert json.loads(restored["s1"].meta[confirmation.EDGE_STATE_META]) == {"driving": False, "gear": "P"}
    assert restored["s2"].confirmation_binding == {} and confirmation.EDGE_STATE_META not in restored["s2"].meta
    slot_plan, _ = PlannerEngine._restore(None, state, inject_confirmed=False)
    assert all(s.confirmation_binding == {} for s in slot_plan.steps)


def test_clients_cannot_supply_the_edge_snapshot():
    from orchestrator.cloud.clients import Clients
    ctx = PlanContext(user_id="u1", prefs={"confirm_state": '{"driving":false,"gear":"P"}'})
    assert "confirm_state" not in Clients._merge_meta(ctx, None)


def test_t2_streaming_never_runs_a_confirmed_step():
    import inspect
    from orchestrator.cloud import loop
    source = inspect.getsource(loop.LoopController)
    assert '.get("confirmed") != "true"' in source


def test_concurrent_clarify_choices_execute_the_option_once():
    calls = []

    class _Clients(_Spy):
        async def call_agent(self, endpoint, intent, slots, ctx, meta):
            calls.append(intent)
            await asyncio.sleep(0.2)
            return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="深圳晴。")

    spy = _Clients()
    engine = _engine(spy)
    option_step = step_record(Step("s1", "info", endpoint="stub:2", intent="info.weather",
                                   slots={"city": "深圳"}))
    pending = SessionState(
        phase="wait_clarify", owner_user_id="u1", operation_id="op-clarify",
        pending_plan={}, clarify={"question": "您是想看天气还是导航过去？", "options": [
            {"label": "看天气", "send_text": "查深圳的天气", "step": option_step},
            {"label": "导航过去", "send_text": "导航去华润大厦"}]})

    async def run():
        await engine.session.save("sess", pending)
        return await asyncio.gather(
            _collect(engine, _req("看天气", operation_id="op-clarify", rid="a")),
            _collect(engine, _req("看天气", operation_id="op-clarify", rid="b")))

    a, b = asyncio.run(run())
    assert calls == ["info.weather"]
    speeches = {_final(x)["speech"] for x in (a, b)}
    assert "深圳晴。" in speeches and speeches - {"深圳晴。"} <= {
        "这条操作已经在处理了，我没有重复执行。", "这条确认对应的操作已经不在了，麻烦您再说一遍需求。"}
