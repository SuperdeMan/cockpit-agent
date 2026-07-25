"""低电量顺路建议（M3 P0）——「服务找人」的样板场景。

订 `vehicle.state.changed`，电量**跌破阈值的变沿**触发一次：查沿途/附近的桩 →
经主动治理器发一条建议。母提案 §4.E 的 DoD 场景（「电量 18% + 导航回家途中 +
顺路有桩」→ 一条合并建议）的电量那一半就是这里；它与场景触发的低电量建议同窗到达时，
由治理器合并成一条。

三条设计约束：
1. **边沿触发 + 生产侧节流**：电量读数在阈值附近会抖（19→20→19），只在「从够 → 不够」
   的变沿发，且同一次低电量周期内不重复（scene triggers 同款）。
2. **拿不到桩就不编**（铁律③）：provider 失败 → **整条不发**。用户没问的时候拿
   「服务不可用」去打扰他，比不说更糟；拿不到位置 → 发不带站点数据的一句话，
   不假装知道附近有什么。
3. **零执行权**：只出话术 + 建议卡，导航要用户自己说（同场景触发 D6）。
"""
from __future__ import annotations

import logging
import time

from runtime.proactive import P_ADVISORY

from .providers.base import GeoPoint

logger = logging.getLogger("agent.charging_planner.low_battery")

TYPE = "charging_advice"
DEDUP_KEY = "charging.low-battery"
TTL_MS = 300_000          # 攒着说的上限 5 分钟——过了就不新鲜了


def _location_point(state: dict):
    """从车况镜像取坐标。没有坐标 → None（调用方退化为不带站点的一句话）。"""
    loc = state.get("location")
    if not isinstance(loc, dict):
        return None
    lat, lng = loc.get("lat"), loc.get("lng") or loc.get("lon")
    if lat is None or lng is None:
        return None
    try:
        return GeoPoint(lat=float(lat), lng=float(lng))
    except (TypeError, ValueError):
        return None


def _soc(state: dict):
    try:
        return float(state.get("battery"))
    except (TypeError, ValueError):
        return None


class LowBatteryWatcher:
    """电量变沿 → 一条低电量建议。纯逻辑，NATS/provider 由外部注入（全离线可测）。"""

    def __init__(self, publish, find_stations, *, threshold: float = 20.0,
                 throttle_s: float = 1800.0, now_fn=time.time, agent_id="charging-planner"):
        self._publish = publish                 # async (payload: dict) -> None
        self._find = find_stations              # async (GeoPoint|None) -> list[station]
        self._threshold = threshold
        self._throttle_s = throttle_s
        self._now = now_fn
        self._agent_id = agent_id
        self._below = False                     # 上一次是否已在阈值下（边沿判据）
        self._last_fire = float("-inf")         # 不是 0——注入时钟下 0 会让首次就撞节流窗

    async def on_state(self, changes: list, state: dict) -> bool:
        """车况变更回调。发了返回 True。"""
        soc = _soc(state)
        if soc is None:
            return False
        below, was = soc < self._threshold, self._below
        self._below = below
        if not below or was:                    # 只在「从够 → 不够」的变沿发
            return False
        now = self._now()
        if now - self._last_fire < self._throttle_s:
            return False
        payload = await self._build(soc, state)
        if not payload:
            return False
        self._last_fire = now
        await self._publish(payload)
        return True

    async def _build(self, soc: float, state: dict) -> dict | None:
        point = _location_point(state)
        stations = []
        if point is not None:
            try:
                stations = await self._find(point) or []
            except Exception as e:            # 主动播报不该因故障发声（含 ProviderError）
                logger.info("低电量建议：查桩失败，本次不打扰（%s）", e)
                return None
        pct = int(soc)
        if stations:
            top = stations[0]
            speech = (f"电量只剩 {pct}% 了，附近有 {len(stations)} 个充电站，"
                      f"最近的是{top.name}（{top.distance_km}km），要导航过去吗？")
            card = {"type": "charging_list", "soc": str(pct),
                    "items": [{"id": s.id, "name": s.name, "available": s.available,
                               "total": s.total, "price": s.price_per_kwh,
                               "distance_km": s.distance_km, "operator": s.operator}
                              for s in stations[:3]]}
        else:
            # 拿不到位置/没搜到 → 只说事实，不编站点
            speech = f"电量只剩 {pct}% 了，要我帮你找个充电桩吗？"
            card = None
        payload = {
            "type": TYPE, "speech": speech, "agent_id": self._agent_id,
            "ts": int(self._now() * 1000),
            "priority": P_ADVISORY, "dedup_key": DEDUP_KEY, "ttl_ms": TTL_MS,
            # 投递时刻由治理器复核：已经充上电了就别再说了
            "conditions": [{"key": "battery", "op": "lt", "value": self._threshold}],
        }
        if card:
            payload["card"] = card
        return payload
