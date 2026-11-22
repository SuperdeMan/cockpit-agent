"""云侧车况镜像单测（M2 P1：Outcome Verifier 的 state_match 求值源）。

全程 fail-open 是本模块的要害：拿不到车况必须表现为「看不见」（空快照 → 求值器 UNKNOWN
→ 不定罪），绝不能表现成「没做成」。
"""
import asyncio
import json
import time

from orchestrator.cloud.state_mirror import STATE_SUBJECT, VehicleStateMirror


class _Msg:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()


def _feed(mirror, changes):
    asyncio.run(mirror._on_state(_Msg({"source": "val", "changes": changes})))


def test_no_nats_url_disables_mirror(monkeypatch):
    monkeypatch.delenv("NATS_URL", raising=False)
    m = VehicleStateMirror()
    assert asyncio.run(m.start()) is False
    assert m.connected is False
    assert m.snapshot() == {}          # 空 → 求值器判 UNKNOWN，不定罪


def test_state_changes_accumulate():
    m = VehicleStateMirror()
    _feed(m, [{"key": "hvac_on", "new": True}])
    _feed(m, [{"key": "hvac_temp", "new": 22}])
    assert m.snapshot() == {"hvac_on": True, "hvac_temp": 22}
    assert m.get("hvac_temp") == 22


def test_later_change_overwrites():
    m = VehicleStateMirror()
    _feed(m, [{"key": "hvac_on", "new": True}])
    _feed(m, [{"key": "hvac_on", "new": False}])
    assert m.snapshot()["hvac_on"] is False


def test_garbage_payload_is_ignored():
    m = VehicleStateMirror()

    class _Bad:
        data = b"not json"
    asyncio.run(m._on_state(_Bad()))
    _feed(m, [{"no_key": 1}, "junk"])
    assert m.snapshot() == {}


def test_stale_mirror_reads_as_blind():
    """链路断了（edge 每 30s 必发全量快照）→ 陈旧值不可信，当作看不见，
    而不是拿旧值去判「没生效」。"""
    clock = [time.monotonic()]
    m = VehicleStateMirror(stale_s=1.0, monotonic=lambda: clock[0])
    _feed(m, [{"key": "hvac_on", "new": True}])
    assert m.snapshot() == {"hvac_on": True}
    clock[0] += 10
    assert m.snapshot() == {}
    assert m.get("hvac_on") is None


def test_stale_disabled_when_zero():
    clock = [time.monotonic()]
    m = VehicleStateMirror(stale_s=0, monotonic=lambda: clock[0])
    _feed(m, [{"key": "hvac_on", "new": True}])
    clock[0] += 10_000
    assert m.snapshot() == {"hvac_on": True}


def test_subject_matches_edge_broadcast():
    """主题名与 orchestrator/edge 的广播、gateway/edge 与 collector 的镜像同源——
    打错一个字就是静默失效。"""
    assert STATE_SUBJECT == "vehicle.state.changed"


def test_close_is_safe_without_connection():
    asyncio.run(VehicleStateMirror().close())


def test_start_requests_snapshot_after_subscribe_and_self_heals_on_delta(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from orchestrator.edge.vehicle_driver import SimulatedVehicleDriver
    from runtime.vehicle_state import SNAPSHOT_REQUEST_SUBJECT

    events = []
    callbacks = {}

    class FakeConnection:
        async def subscribe(self, subject, cb=None):
            events.append(("subscribe", subject))
            callbacks[subject] = cb

        async def flush(self):
            events.append(("flush", None))

        async def publish(self, subject, data):
            events.append(("publish", subject, data))

        async def close(self):
            pass

    fake_nats = ModuleType("nats")

    async def fake_connect(url, **kwargs):
        return FakeConnection()

    fake_nats.connect = fake_connect
    monkeypatch.setenv("NATS_URL", "nats://fake:4222")
    monkeypatch.setitem(sys.modules, "nats", fake_nats)

    clock = [100.0]
    mirror = VehicleStateMirror(monotonic=lambda: clock[0])

    async def run():
        assert await mirror.start() is True
        assert events == [
            ("subscribe", STATE_SUBJECT),
            ("flush", None),
            ("publish", SNAPSHOT_REQUEST_SUBJECT, b"{}"),
        ]

        driver = SimulatedVehicleDriver("v1")
        _ = driver.observation(snapshot=True)
        driver.state["hvac_on"] = True
        delta = driver.observation(changes=[{"key": "hvac_on", "old": False, "new": True}])

        clock[0] += 2.0
        result = await callbacks[STATE_SUBJECT](SimpleNamespace(data=json.dumps(delta).encode()))
        assert not result.accepted and result.reason == "snapshot_required"
        assert events[-1] == ("publish", SNAPSHOT_REQUEST_SUBJECT, b"{}")

        snap = driver.observation(snapshot=True)
        result = await callbacks[STATE_SUBJECT](SimpleNamespace(data=json.dumps(snap).encode()))
        assert result.accepted
        assert mirror.get("hvac_on") is True

    asyncio.run(run())
