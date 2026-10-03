"""Two signed simulator sources through actual consumer entry points; zero network."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents._sdk.shared_state import SCENE_ACTIVE, vehicle_scoped
from agents._sdk.testing import make_context
from agents.charging_planner.src.agent import ChargingPlannerAgent
from agents.info.src.agent import InfoAgent
from agents.reminder.src.agent import ReminderAgent
from agents.road_safety.src.agent import RoadSafetyAgent
from agents.scene_orchestrator.src.agent import SceneOrchestratorAgent
from agents.scene_orchestrator.src.state_mirror import StateMirror
from observability.collector.server import Hub, create_app
from observability.collector.store import CollectorStore
from orchestrator.cloud.state_mirror import VehicleStateMirror
from proactive.mirror import VehicleMirror
from runtime.vehicle_state import TrustPolicy, VehicleStateStore
from scripts.probe_vehicle_state_simulation import SimulationClock, simulation_source, run


@pytest.fixture
def lab():
    clock = SimulationClock()
    a, sa, _ = simulation_source("v1"); b, sb, _ = simulation_source("v2")
    policy = TrustPolicy({a.key_id:a, b.key_id:b})
    clocks = {"wall_ms":lambda: clock.ms, "monotonic":lambda: clock.mono}
    seq = {"v1":0, "v2":0}
    def message(vehicle, **values):
        binding, signer = (a,sa) if vehicle == "v1" else (b,sb)
        seq[vehicle] += 1
        payload = {"vehicle_id":vehicle, "source_id":binding.source_id, "source_epoch":"boot-1",
                   "epoch_started_at_ms":1_800_000_000_000, "source_seq":seq[vehicle],
                   "emitted_at_ms":clock.ms, "snapshot":True,
                   "signals":[{"key":k, "value":v, "quality":"good", "unit":binding.unit(k), "observed_at_ms":clock.ms}
                              for k,v in values.items()]}
        return SimpleNamespace(data=json.dumps(signer.envelope(payload)).encode())
    return SimpleNamespace(clock=clock, policy=policy, clocks=clocks, message=message,
                           store=lambda:VehicleStateStore(policy, **clocks))


@pytest.mark.asyncio
async def test_cloud_scene_proactive_and_collector_share_identity_and_expiry(lab):
    cloud = VehicleStateMirror(policy=lab.policy, **lab.clocks)
    scene = StateMirror(policy=lab.policy, **lab.clocks)
    proactive = VehicleMirror(policy=lab.policy, **lab.clocks)
    collector = CollectorStore(); collector.vehicle_states = lab.store()
    scoped, legacy = AsyncMock(), AsyncMock()
    scene.on_vehicle_change(scoped); scene.on_change(legacy)
    for vehicle, battery in (("v1",15),("v2",88)):
        msg = lab.message(vehicle, battery=battery, gear="P")
        await cloud._on_state(msg); await scene._on_state(msg); proactive.apply(msg.data); collector.apply_state(msg.data)
    for store in (cloud.store, scene.store, proactive.store, collector.vehicle_states):
        assert store.snapshot("v1") == {"battery":15,"gear":"P"}
        assert store.snapshot("v2")["battery"] == 88
        assert store.snapshot("") == {}
    assert [call.args[2] for call in scoped.await_args_list] == ["v1","v2"]
    assert legacy.await_count == 1
    lab.clock.advance(1001)
    for store in (cloud.store, scene.store, proactive.store, collector.vehicle_states):
        assert store.snapshot("v1") == {"battery":15}
        assert store.view("v1")["signals"]["gear"]["quality"] == "stale"


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_class,primary,others", [
    (ChargingPlannerAgent,"_low_battery","_low_batteries"), (ReminderAgent,"_geofence","_geofences")])
async def test_charging_and_reminder_forward_only_the_selected_vehicle(lab, agent_class, primary, others):
    agent = agent_class(); agent._vehicle_states = lab.store()
    watchers = {v:SimpleNamespace(on_state=AsyncMock()) for v in ("v1","v2")}
    setattr(agent,primary,watchers["v1"]); setattr(agent,others,{"v2":watchers["v2"]})
    for vehicle,battery in (("v1",15),("v2",88)):
        await agent._on_state_event(lab.message(vehicle,battery=battery,location={"city":vehicle}))
    assert watchers["v1"].on_state.await_args.args[1]["battery"] == 15
    assert watchers["v2"].on_state.await_args.args[1]["battery"] == 88
    invalid = lab.message("v2",battery=2); envelope=json.loads(invalid.data); envelope["signature"]="invalid"
    await agent._on_state_event(SimpleNamespace(data=json.dumps(envelope).encode()))
    assert all(w.on_state.await_count==1 for w in watchers.values())


@pytest.mark.asyncio
async def test_road_safety_same_city_dedup_is_scoped_to_vehicle(lab):
    agent = RoadSafetyAgent(); agent._vehicle_states=lab.store()
    agent._evaluate_hazard=AsyncMock(return_value="rain")
    agent._maybe_broadcast=AsyncMock()
    for vehicle in ("v1","v2","v1"):
        await agent._on_state_event(lab.message(vehicle,location={"city":"深圳"}))
    assert [call.kwargs["vehicle_id"] for call in agent._maybe_broadcast.await_args_list] == ["v1","v2"]


@pytest.mark.asyncio
async def test_morning_briefing_does_not_consume_the_other_cars_daily_slot(lab):
    agent=InfoAgent(); agent._vehicle_states=lab.store()
    agent._is_morning_drive=lambda event: bool(event["changes"])
    agent._publish_morning_briefing=AsyncMock()
    for vehicle in ("v1","v2"):
        await agent._on_state_event(lab.message(vehicle,gear="D"))
    assert [call.kwargs["vehicle_id"] for call in agent._publish_morning_briefing.await_args_list] == ["v1","v2"]


@pytest.mark.asyncio
async def test_scene_active_has_one_authority_per_vehicle_including_rollback():
    agent=SceneOrchestratorAgent(); kv={SCENE_ACTIVE:{"scene_id":"legacy"}}
    async def load(key): return kv.get(key)
    async def save(key,value): kv[key]=value; return True
    contexts={v:make_context(vehicle_id=v) for v in ("v1","v2")}
    for ctx in contexts.values(): ctx.load_shared_state=load; ctx.save_shared_state=save
    assert await agent._load_kv(contexts["v1"],SCENE_ACTIVE)=={"scene_id":"legacy"}
    assert await agent._load_kv(contexts["v2"],SCENE_ACTIVE)=={}
    assert await agent._save_kv(contexts["v2"],SCENE_ACTIVE,{"scene_id":"car-b"})
    assert kv[SCENE_ACTIVE]=={"scene_id":"legacy"}
    assert kv[vehicle_scoped(SCENE_ACTIVE,"v2")]=={"scene_id":"car-b"}
    assert await agent._save_kv(contexts["v1"],SCENE_ACTIVE,{})
    assert await agent._load_kv(contexts["v1"],SCENE_ACTIVE)=={}
    assert vehicle_scoped(SCENE_ACTIVE,"v1") not in kv
    kv[SCENE_ACTIVE]={"scene_id":"rollback-writer"}
    assert await agent._load_kv(contexts["v1"],SCENE_ACTIVE)=={"scene_id":"rollback-writer"}
    assert await agent._load_kv(contexts["v2"],SCENE_ACTIVE)=={"scene_id":"car-b"}


@pytest.mark.asyncio
async def test_collector_serializes_fresh_snapshots_and_sends_expiry_without_events(lab):
    class Socket:
        def __init__(self): self.sent=[]
        async def accept(self): pass
        async def send_text(self, text): self.sent.append(json.loads(text)); await asyncio.sleep(0)
    store=CollectorStore(); store.vehicle_states=lab.store(); hub=Hub()
    a,b=Socket(),Socket(); await hub.join(a,"v1"); await hub.join(b,"v2")
    store.apply_state(lab.message("v1",gear="P").data)
    await hub.send_observation(a,store,initial=True); await hub.send_observation(b,store,initial=True)
    store.apply_state(lab.message("v1",gear="D").data)
    await asyncio.gather(hub.broadcast_vehicle(store,"v1"),hub.send_observation(a,store))
    assert a.sent[-1]["observation"]["state"]=={"gear":"D"}
    assert len(a.sent)==2 and len(b.sent)==1
    lab.clock.advance(1001); await hub.send_observation(a,store)
    assert a.sent[-1]["observation"]["state"]=={}
    assert a.sent[-1]["observation"]["signals"]["gear"]["quality"]=="stale"


def test_collector_http_vehicle_scope(lab):
    from fastapi.testclient import TestClient
    from runtime import obs_access
    store=CollectorStore(); store.vehicle_states=lab.store()
    store.apply_state(lab.message("v1",battery=15).data); store.apply_state(lab.message("v2",battery=88).data)
    key=obs_access.derive_key("vehicle-scope-test-secret-"+"v"*32)    # collector 读写要运维凭据
    with TestClient(create_app(store=store, operator_key=key),
                    headers=obs_access.headers(obs_access.issue(key))) as client:
        assert client.get("/api/vehicle/state?vehicle_id=v2").json()=={"battery":88}
        assert client.get("/api/vehicle/state?vehicle_id=absent").json()=={}
        assert client.get("/api/vehicle/observation?vehicle_id=v1").json()["signals"]["battery"]["authenticated"]


def test_fault_lab_is_replayable_and_ack_is_independent_from_state():
    first, second=run(12),run(12)
    assert first==second and first["passed"]
    assert first["receipt"]["status"]=="unknown" and not first["causality_proven"]
