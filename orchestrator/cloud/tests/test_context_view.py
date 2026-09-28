"""Authorization inputs, not model metadata, decide what can enter a prompt."""
import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents._sdk.base import Context
from orchestrator.cloud.clients import Clients
from orchestrator.cloud.context import ContextManager, Focus, WorkingSet
from orchestrator.cloud.context_view import ActiveViews, ContextChanged, RequestView
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder
from runtime.context_access import allowed, project_meta
from security.scopes import is_scope_covered


def ctx(*scopes):
    return PlanContext(user_id="owner", session_id="session", vehicle_id="v1",
                       granted_permissions=list(scopes))


def test_write_permission_does_not_grant_context_reads():
    c = ctx("profile.write", "vehicle.control", "network.external")
    assert not allowed(c, "profile")
    assert not allowed(c, "vehicle_state")
    assert is_scope_covered("profile.read", {"profile"})
    for receiver in (None, [], ["location", "vehicle_state", "vision", "candidates"]):
        result = project_meta(c, {"current_lat": "SECRET", "vehicle_battery": "70",
                                  "vision_frame_id": "SECRET", "focus_candidate_set": "SECRET"}, receiver)
        assert result == {"memory_enabled": "false"}


def test_meta_is_filtered_after_merge_and_memory_off_cannot_be_undone():
    c = ctx("profile.read")
    c.prefs = {"memory_enabled": "false", "current_lat": "SECRET", "answer_length": "short"}
    out = Clients._merge_meta(c, {"current_lng": "SECRET", "memory_enabled": "true",
                                 "granted_scopes": "location.read,admin"}, ["location"])
    assert "SECRET" not in json.dumps(out)
    assert out["memory_enabled"] == "false"
    assert out["granted_scopes"] == "profile.read"
    assert out["answer_length"] == "short"


def test_unbound_legacy_stream_cannot_receive_sensor_meta():
    c = ctx("location.read")
    c.prefs = {"current_lat": "SECRET", "current_lng": "SECRET"}
    assert "SECRET" not in json.dumps(Clients._merge_meta(c, {}))
    assert Clients._merge_meta(c, {}, ["location"])["current_lat"] == "SECRET"


def test_pinned_focus_does_not_override_field_permissions():
    c = ctx("profile.read")
    w = WorkingSet(history=[{"role": "user", "text": "owner supplied text"}],
                   memories=[{"text": "owner preference"}],
                   focus=Focus(last_destination="SECRET_LOCATION", destination_lat=12,
                               active_task={"slots": {"address": "SECRET_LOCATION"}},
                               by_occupant={"other": {"secret": "SECRET_OTHER"}},
                               session_constraints={"no_spicy": True}))
    w.bind_view(c)
    prompt = w.render_context()
    assert "SECRET" not in prompt
    assert "owner supplied text" in prompt
    assert "owner preference" in prompt
    assert "不吃辣" in prompt
    assert w.focus.by_occupant == {}


@pytest.mark.parametrize("change", ["scope", "user", "vehicle", "occupant", "request", "memory_off"])
def test_inflight_capsule_cannot_be_rebound_or_revived(change):
    c = ctx("profile.read", "location.read")
    w = WorkingSet(history=[{"role": "user", "text": "SECRET"}]).bind_view(c)
    if change == "scope":
        c.granted_permissions.remove("profile.read")
    elif change == "memory_off":
        c.prefs["memory_enabled"] = "false"
    else:
        setattr(c, change + "_id", "other")
    with pytest.raises(ContextChanged):
        w.render_context()
    c.granted_permissions.append("profile.read")
    with pytest.raises(ContextChanged):
        w.render_context()


def test_memory_denial_is_off_and_makes_no_backend_request():
    clients = SimpleNamespace(list_agents=AsyncMock(return_value=[]),
                              get_session_read=AsyncMock(), recall_read=AsyncMock())
    w = asyncio.run(ContextManager(clients).assemble("test", ctx("network.external")))
    assert w.history_state == w.memory_state == "off"
    clients.get_session_read.assert_not_awaited()
    clients.recall_read.assert_not_awaited()
    assert w.source_states["vehicle"]["access"] == "denied"


@pytest.mark.parametrize("state", ["none", "unavailable", "found"])
def test_authorized_read_states_are_preserved(state):
    values = [{"text": "SECRET", "role": "user"}] if state == "found" else []
    clients = SimpleNamespace(list_agents=AsyncMock(return_value=[]),
                              get_session_read=AsyncMock(return_value=(values, state)),
                              recall_read=AsyncMock(return_value=(values, state)))
    w = asyncio.run(ContextManager(clients).assemble("test", ctx("profile.read")))
    assert w.history_state == w.memory_state == state
    assert w.source_states["memory"]["state"] == state


def test_revocation_while_reading_discards_the_entire_capsule():
    c = ctx("profile.read")
    async def read(*args, **kwargs):
        c.granted_permissions.clear()
        return [{"text": "SECRET"}], "found"
    clients = SimpleNamespace(list_agents=AsyncMock(return_value=[]), get_session_read=read,
                              recall_read=read)
    with pytest.raises(ContextChanged):
        asyncio.run(ContextManager(clients).assemble("test", c))


def test_actual_model_request_omits_private_history_and_discards_late_output():
    c = ctx("profile.read")
    w = WorkingSet(history=[{"role": "user", "text": "SECRET_HISTORY"}]).bind_view(c)
    seen = []
    async def model(messages):
        seen.append(messages)
        c.granted_permissions.clear()
        return '{"steps":[]}'
    planner = PlanBuilder(llm_fn=model, registry_fn=AsyncMock(return_value=[]))
    catalog = SimpleNamespace(semantic_mapping_text="catalog")
    with pytest.raises(ContextChanged):
        asyncio.run(planner._llm_plan("question", catalog, w))
    assert "SECRET_HISTORY" in json.dumps(seen)
    denied = WorkingSet(history=[{"role": "user", "text": "SECRET_DENIED"}]).bind_view(ctx())
    assert "SECRET_DENIED" not in planner._planner_user_msg("question", catalog, denied)


def test_privacy_event_invalidates_only_matching_active_owner():
    active = ActiveViews()
    first, second = ctx("profile.read"), ctx("profile.read")
    second.user_id = "other"
    one, two = active.bind(first), active.bind(second)
    active.invalidate_owner("owner")
    with pytest.raises(ContextChanged):
        one.check()
    two.check()


def test_vehicle_view_uses_authority_not_forged_metadata_and_hides_location():
    c = ctx("vehicle.read.state")
    c.prefs = {"vehicle_battery": "999"}
    fresh = {"quality": "good", "freshness": "bounded", "expires_at_ms": int(time.time() * 1000) + 60000}
    observed = {"version": 2, "vehicle_id": "v1", "state": {"battery": 71, "gear": "P", "location": {"lat": 1}},
                "signals": {"battery": fresh, "gear": {"quality": "stale"},
                            "location": fresh}}
    c.context_view = RequestView(c, lambda vehicle: observed)
    values, meta = c.context_view.vehicle()
    assert values == {"battery": 71}
    assert "location" not in meta["signals"]
    out = Clients._merge_meta(c, {"vehicle_battery": "999"}, ["vehicle_state"])
    assert out["vehicle_battery"] == "71"
    observed["vehicle_id"] = "other"
    assert c.context_view.vehicle()[1]["state"] == "unavailable"
    assert "vehicle_battery" not in Clients._merge_meta(c, {}, ["vehicle_state"])


def test_sdk_memory_cannot_bypass_the_planner_projection():
    backend = SimpleNamespace(recall_read=AsyncMock(return_value=([{"text": "SECRET"}], "found")))
    c = Context("session", "owner", "v1", backend, meta={"granted_scopes": "profile.write"})
    assert asyncio.run(c.recall_read()) == ([], "off")
    backend.recall_read.assert_not_awaited()
    c = Context("session", "owner", "v1", backend, meta={"granted_scopes": "profile.read"})
    assert asyncio.run(c.recall_read())[1] == "found"
    async def late(*args, **kwargs):
        c.vehicle_id = "other"
        return [{"text": "SECRET"}], "found"
    backend.recall_read = late
    assert asyncio.run(c.recall_read()) == ([], "off")


def test_narrower_request_cancels_existing_rpc_but_not_another_owner():
    async def run():
        active = ActiveViews()
        ready = asyncio.Event()
        async def inflight():
            active.bind(ctx("profile.read", "location.read"))
            ready.set()
            await asyncio.Future()
        task = asyncio.create_task(inflight())
        await ready.wait()
        other = ctx("location.read")
        other.user_id = "other"
        active.bind(other)
        assert not task.cancelling()
        replacement = active.bind(ctx("location.read"))
        with pytest.raises(asyncio.CancelledError):
            await task
        replacement.check()
    asyncio.run(run())


def test_fresh_value_receipt_limits_rpc_and_expiration_cannot_refresh_itself():
    clock = {"wall": 1000.0, "mono": 10.0}
    c = ctx("vehicle.read.state")
    observation = {"version": 2, "vehicle_id": "v1", "state": {"battery": 71},
                   "signals": {"battery": {"quality": "good", "freshness": "bounded", "expires_at_ms": 1002000}}}
    view = RequestView(c, lambda vehicle: observation, wall=lambda: clock["wall"], monotonic=lambda: clock["mono"])
    view.vehicle(consume=True, keys=("battery",))
    assert view.remaining(30) == 2
    clock["mono"] += 3
    # Even if a later source update arrives, it cannot retroactively freshen
    # the facts used by the already-running operation.
    observation["signals"]["battery"]["expires_at_ms"] += 90000
    with pytest.raises(ContextChanged):
        view.vehicle()


def test_stored_pending_context_is_not_sent_to_model_without_profile_read():
    c = ctx("network.external")
    c.pending_operation_id = "op-existing"
    model = AsyncMock()
    planner = PlanBuilder(llm_fn=model, registry_fn=AsyncMock(return_value=[]))
    with pytest.raises(ContextChanged):
        asyncio.run(planner.replan("SECRET_OLD_GOAL", [{"speech": "SECRET_RESULT"}], [], c))
    model.assert_not_awaited()


def test_sdk_vehicle_fetch_uses_projection_and_never_legacy_memory_values():
    backend = SimpleNamespace(get_context=AsyncMock(return_value={"vehicle.battery": "999"}))
    c = Context("session", "owner", "v1", backend, meta={"granted_scopes": "vehicle.read.state"})
    assert asyncio.run(c.fetch("vehicle.battery")) == {}
    assert c.read_states["vehicle_state"] == "unavailable"
    c._projection_meta = {"vehicle_observation": json.dumps({
        "version": 2, "vehicle_id": "v1", "state": {"battery": 71},
        "signals": {"battery": {"quality": "good", "freshness": "bounded", "expires_at_ms": int(time.time()*1000)+60000}}})}
    assert asyncio.run(c.fetch("vehicle.battery")) == {"vehicle.battery": "71"}
    backend.get_context.assert_not_awaited()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("granted,declared,expected", [
    ("profile.read", ["location"], False),
    ("location.read", [], False),
    ("location.read", ["location"], True),
])
def test_real_sdk_entry_filters_before_handler(stream, granted, declared, expected):
    from agents._sdk.server import _Servicer
    from agents._sdk.result import AgentResult
    from cockpit.agent.v1 import agent_pb2
    from cockpit.common.v1 import common_pb2
    from runtime import capability_contract as contract
    mf = agent_pb2.AgentManifest(agent_id="context-fixture", context_scopes=declared)
    cap = mf.capabilities.add(intent="fixture.read", effect="read")
    cap.contract.update(contract.declaration([], "read", legacy=False))
    seen = []
    class Echo:
        manifest = mf
        memory = SimpleNamespace()
        async def handle(self, intent, context, meta):
            seen.append(meta)
            return AgentResult(speech="ok")
        async def handle_stream(self, intent, context, meta):
            seen.append(meta)
            yield "final", AgentResult(speech="ok")
    request = agent_pb2.ExecuteRequest(
        intent=common_pb2.Intent(name="fixture.read"),
        context=common_pb2.ContextRef(user_id="owner", vehicle_id="v1"),
        meta={"granted_scopes": granted, "current_lat": "SECRET_LOCATION",
              contract.HEADER: contract.capability_digest(mf, cap)})
    server = _Servicer(Echo())
    async def run():
        if stream:
            async for _ in server.ExecuteStream(request, None):
                pass
        else:
            await server.Execute(request, None)
    asyncio.run(run())
    assert bool(seen) and ("current_lat" in seen[0]) is expected


def test_real_stream_client_passes_receiver_demand_and_stops_late_frames():
    from cockpit.agent.v1 import agent_pb2
    c = ctx("location.read")
    c.prefs = {"current_lat": "REAL_LOCATION"}
    c.context_view = RequestView(c)
    captured = []
    async def stream(request, timeout):
        captured.append(request)
        yield agent_pb2.ExecuteEvent(speech_delta="allowed")
        c.granted_permissions.clear()
        yield agent_pb2.ExecuteEvent(speech_delta="SECRET_LATE")
    client = Clients()
    client._agent_stub = lambda _: SimpleNamespace(ExecuteStream=stream)
    delivered = []
    async def run():
        with pytest.raises(ContextChanged):
            async for kind, value in client.call_agent_stream(
                    "unused", "test.read", {}, c,
                    {"current_lat": "FORGED"}, context_scopes=["location"]):
                delivered.append(value)
    asyncio.run(run())
    assert delivered == ["allowed"]
    assert captured[0].meta["current_lat"] == "REAL_LOCATION"
