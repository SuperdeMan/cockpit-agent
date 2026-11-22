"""Read-only vehicle observations for proactive conditions and driving load.

The governor must select the vehicle named by a scoped notification. Missing
identity yields no vehicle facts, rather than borrowing the demo vehicle.
"""
from __future__ import annotations

import logging
import math
import os
import time

from runtime.vehicle_state import (
    LEGACY_VEHICLE,
    SNAPSHOT_REQUEST_SUBJECT,
    STATE_SUBJECT,
    VehicleStateStore,
)

logger = logging.getLogger("proactive.mirror")


class VehicleMirror:
    def __init__(self, *, policy=None, wall_ms=None, monotonic=None):
        clocks = {"wall_ms": wall_ms}
        if monotonic is not None:
            clocks["monotonic"] = monotonic
        self._mono = monotonic or time.monotonic
        self._nc = None
        self._last_snapshot_req = -math.inf
        self.store = VehicleStateStore(policy, **clocks)

    @property
    def state(self) -> dict:
        return self.snapshot(LEGACY_VEHICLE)

    def snapshot(self, vehicle_id=LEGACY_VEHICLE) -> dict:
        return self.store.snapshot(vehicle_id)

    def apply(self, raw: bytes):
        return self.store.ingest(raw)

    async def _request_snapshot(self, *, force: bool = False) -> None:
        if self._nc is None or not hasattr(self._nc, "publish"):
            return
        now = self._mono()
        if not force and now - self._last_snapshot_req < 1.0:
            return
        self._last_snapshot_req = now
        try:
            await self._nc.publish(SNAPSHOT_REQUEST_SUBJECT, b"{}")
        except Exception as exc:
            logger.debug("proactive 请求车况快照失败：%s", exc)

    async def subscribe(self, nc) -> bool:
        if nc is None:
            logger.info("车况镜像禁用（无 NATS 连接）——驾驶负荷闸将始终放行")
            return False
        self._nc = nc
        # cb 必须是协程函数本身：nats-py 用 inspect 判定，lambda 返回协程会被拒
        # （"nats: must use coroutine for subscriptions"）——真栈首启才暴露。
        await nc.subscribe(STATE_SUBJECT, cb=self._on_msg)
        if hasattr(nc, "flush"):
            await nc.flush()
        await self._request_snapshot(force=True)
        logger.info("已订阅 %s，车况镜像开启", STATE_SUBJECT)
        return True

    async def _on_msg(self, msg) -> None:
        result = self.apply(msg.data)
        if not result.accepted and result.reason == "snapshot_required":
            await self._request_snapshot()


def nats_url() -> str:
    return os.getenv("NATS_URL", "")
