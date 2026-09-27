"""Vehicle-scoped scene observations; one subscription, explicit callback scope.

Unscoped legacy callbacks belong only to the configured PoC vehicle. Foreground
scene reads must pass the request vehicle. Expiry and identity share the runtime
validator; no caller can refresh all signals by publishing a single delta.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable

from runtime.vehicle_state import VehicleStateStore, LEGACY_VEHICLE
from runtime.proactive import SKIPPED, publish_proactive

logger = logging.getLogger("agent.scene.mirror")

STATE_SUBJECT = "vehicle.state.changed"

ChangeCb = Callable[[list, dict], Awaitable[None]]


class StateMirror:
    def __init__(self, *, policy=None, wall_ms=None, monotonic=None):
        clocks = {"wall_ms": wall_ms}
        if monotonic is not None:
            clocks["monotonic"] = monotonic
        self.store = VehicleStateStore(policy, **clocks)
        self._nc = None
        self._cbs: list[tuple[ChangeCb, str]] = []
        self._vehicle_cbs = []

    @property
    def connected(self) -> bool:
        return self._nc is not None

    async def start(self) -> bool:
        """订阅状态广播。无 NATS_URL / 连接失败 → 静默禁用（镜像恒空），不影响请求-响应。"""
        url = os.getenv("NATS_URL", "")
        if not url:
            logger.info("scene: NATS_URL 未设置，车况镜像禁用（退反向默认表恢复）")
            return False
        try:
            import nats
            self._nc = await nats.connect(url, max_reconnect_attempts=-1)
            await self._nc.subscribe(STATE_SUBJECT, cb=self._on_state)
        except Exception as e:
            logger.warning("scene: NATS 连接失败，车况镜像禁用：%s", e)
            self._nc = None
            return False
        logger.info("scene: 已订阅 %s，车况镜像开启", STATE_SUBJECT)
        return True

    def on_change(self, cb: ChangeCb, *, vehicle_id=LEGACY_VEHICLE) -> None:
        """挂一个变更消费方：cb(changes, full_state)。异常由本模块吞掉（fail-open）。"""
        self._cbs.append((cb, vehicle_id))

    def on_vehicle_change(self, cb) -> None:
        """cb(changes, state, vehicle_id); identity comes from validated ingestion."""
        self._vehicle_cbs.append(cb)

    async def _on_state(self, msg) -> None:
        accepted = self.store.ingest(msg.data)
        if not accepted.accepted:
            return
        for cb in list(self._vehicle_cbs):
            try:
                await cb(list(accepted.changes), self.store.snapshot(accepted.vehicle_id), accepted.vehicle_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("scene: 车辆状态消费方异常（忽略）：%s", e)
        for cb, vehicle_id in list(self._cbs):
            if vehicle_id != accepted.vehicle_id:
                continue
            try:
                await cb(list(accepted.changes), self.store.snapshot(vehicle_id))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("scene: 状态消费方异常（忽略）：%s", e)

    # ── 读 ──
    def snapshot(self, vehicle_id=LEGACY_VEHICLE) -> dict:
        return self.store.snapshot(vehicle_id)

    def get(self, key: str, default=None, *, vehicle_id=LEGACY_VEHICLE):
        return self.snapshot(vehicle_id).get(key, default)

    def capture(self, keys, *, vehicle_id=LEGACY_VEHICLE) -> dict:
        snapshot = self.snapshot(vehicle_id)
        return {k: snapshot.get(k) for k in keys}

    # ── 写（proactive 播报；P2 Verify / P3 触发用）──
    async def publish(self, payload: dict) -> bool:
        """经主动治理器发（M3 P0）。治理器缺席 → runtime 客户端自动直发老主题。"""
        if not self._nc:
            logger.info("scene: NATS 未连接，proactive 未推送：%s",
                        str(payload.get("speech", ""))[:40])
            return False
        return await publish_proactive(self._nc, payload) != SKIPPED

    async def close(self) -> None:
        if self._nc:
            try:
                await self._nc.close()
            finally:
                self._nc = None
