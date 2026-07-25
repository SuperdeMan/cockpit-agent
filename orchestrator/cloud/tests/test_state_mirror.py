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
    m = VehicleStateMirror(stale_s=1.0)
    _feed(m, [{"key": "hvac_on", "new": True}])
    assert m.snapshot() == {"hvac_on": True}
    m._updated_at = time.time() - 10
    assert m.snapshot() == {}
    assert m.get("hvac_on") is None


def test_stale_disabled_when_zero():
    m = VehicleStateMirror(stale_s=0)
    _feed(m, [{"key": "hvac_on", "new": True}])
    m._updated_at = time.time() - 10_000
    assert m.snapshot() == {"hvac_on": True}


def test_subject_matches_edge_broadcast():
    """主题名与 orchestrator/edge 的广播、gateway/edge 与 collector 的镜像同源——
    打错一个字就是静默失效。"""
    assert STATE_SUBJECT == "vehicle.state.changed"


def test_close_is_safe_without_connection():
    asyncio.run(VehicleStateMirror().close())
