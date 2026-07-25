"""低电量主动建议（M3 P0）契约测试。"""
from __future__ import annotations

import pytest

from agents.charging_planner.src.low_battery import DEDUP_KEY, LowBatteryWatcher
from agents.charging_planner.src.providers.base import ChargingStation


def station(name="国网充电站", km=1.2):
    return ChargingStation(id="s1", name=name, available=3, total=6,
                           price_per_kwh="1.2", operator="国网", distance_km=km)


class Cap:
    def __init__(self):
        self.sent: list[dict] = []

    async def publish(self, p):
        self.sent.append(p)


def watcher(cap, find=None, **kw):
    async def _find(_point):
        return find if find is not None else []
    return LowBatteryWatcher(cap.publish, _find, now_fn=lambda: 1000.0, **kw)


LOC = {"battery": 18, "location": {"lat": 39.9, "lng": 116.4}}


@pytest.mark.asyncio
async def test_edge_trigger_fires_once_not_on_every_broadcast():
    cap = Cap()
    w = watcher(cap, find=[station()])
    assert await w.on_state([], LOC) is True
    # 电量还在阈值下，后续每条广播都不该再响（边沿触发）
    assert await w.on_state([], {**LOC, "battery": 17}) is False
    assert await w.on_state([], {**LOC, "battery": 16}) is False


@pytest.mark.asyncio
async def test_no_fire_above_threshold():
    cap = Cap()
    w = watcher(cap, find=[station()])
    assert await w.on_state([], {**LOC, "battery": 55}) is False
    assert cap.sent == []


@pytest.mark.asyncio
async def test_recovers_edge_after_charging_up():
    cap = Cap()
    w = watcher(cap, find=[station()], throttle_s=0)
    assert await w.on_state([], LOC) is True
    assert await w.on_state([], {**LOC, "battery": 80}) is False   # 充上电了
    assert await w.on_state([], {**LOC, "battery": 15}) is True    # 再次跌破 → 新的变沿


@pytest.mark.asyncio
async def test_producer_side_throttle_blocks_oscillation():
    cap = Cap()
    w = watcher(cap, find=[station()], throttle_s=1800)
    assert await w.on_state([], LOC) is True
    await w.on_state([], {**LOC, "battery": 21})      # 抖回阈值上
    assert await w.on_state([], {**LOC, "battery": 19}) is False   # 节流窗内不重复
    assert len(cap.sent) == 1


@pytest.mark.asyncio
async def test_payload_declares_governance_and_recheck_condition():
    cap = Cap()
    w = watcher(cap, find=[station()])
    await w.on_state([], LOC)
    p = cap.sent[0]
    assert p["priority"] == "advisory" and p["dedup_key"] == DEDUP_KEY
    assert p["ttl_ms"] > 0
    # 投递时刻复核：已经充上电就别再说了
    assert p["conditions"] == [{"key": "battery", "op": "lt", "value": 20.0}]
    assert "18%" in p["speech"] and "国网充电站" in p["speech"]
    assert p["card"]["type"] == "charging_list"


@pytest.mark.asyncio
async def test_provider_failure_stays_silent_never_fabricates():
    """铁律③：拿不到桩就不编。主动播报失败 → **整条不发**，不拿「服务不可用」打扰用户。"""
    cap = Cap()

    async def boom(_p):
        raise RuntimeError("provider down")
    w = LowBatteryWatcher(cap.publish, boom, now_fn=lambda: 1000.0)
    assert await w.on_state([], LOC) is False
    assert cap.sent == []


@pytest.mark.asyncio
async def test_no_location_sends_fact_only_without_station_claims():
    cap = Cap()
    w = watcher(cap, find=[station()])
    assert await w.on_state([], {"battery": 18}) is True     # 无坐标
    p = cap.sent[0]
    assert "18%" in p["speech"] and "充电桩" in p["speech"]
    assert "card" not in p, "拿不到位置时不该带站点卡（会是编的）"


@pytest.mark.asyncio
async def test_missing_battery_reading_is_noop():
    cap = Cap()
    w = watcher(cap, find=[station()])
    assert await w.on_state([], {"speed_kmh": 30}) is False
