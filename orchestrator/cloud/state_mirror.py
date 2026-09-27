"""Vehicle-scoped, read-only observations for the Outcome Verifier.

The NATS subject is unchanged. Validation, origin and per-signal freshness are
shared with other Python consumers in runtime.vehicle_state. Missing or invalid
observations remain UNKNOWN; they never grant command authority.
"""
from __future__ import annotations

import asyncio
import logging
import os
from runtime.vehicle_state import VehicleStateStore, TrustPolicy, LEGACY_VEHICLE

logger = logging.getLogger("planner.state_mirror")

STATE_SUBJECT = "vehicle.state.changed"
# 镜像陈旧上限：超过这么久没收到任何广播 = 链路断了（edge 每 30s 必发全量快照），
# 此时镜像内容不可信 → 当作"看不见"（UNKNOWN），而不是拿陈旧值去定罪。
DEFAULT_STALE_S = 180.0


class VehicleStateMirror:
    def __init__(self, stale_s: float | None = None, *, policy=None, wall_ms=None, monotonic=None):
        self._nc = None
        self._stale_s = stale_s if stale_s is not None else _stale_s()
        policy = policy or TrustPolicy.from_env(legacy_ttl_ms=max(0, int(self._stale_s * 1000)))
        clock = {"wall_ms": wall_ms}
        if monotonic is not None:
            clock["monotonic"] = monotonic
        self.store = VehicleStateStore(policy, **clock)

    @property
    def connected(self) -> bool:
        return self._nc is not None

    async def start(self) -> bool:
        url = os.getenv("NATS_URL", "")
        if not url:
            logger.info("cloud: NATS_URL 未设置，车况镜像禁用（state_match 对账恒 unknown）")
            return False
        try:
            import nats
            self._nc = await nats.connect(url, max_reconnect_attempts=-1)
            await self._nc.subscribe(STATE_SUBJECT, cb=self._on_state)
        except Exception as e:
            logger.warning("cloud: NATS 连接失败，车况镜像禁用：%s", e)
            self._nc = None
            return False
        logger.info("cloud: 已订阅 %s，车况镜像开启（供执行后对账）", STATE_SUBJECT)
        return True

    async def _on_state(self, msg) -> None:
        return self.store.ingest(msg.data)

    def snapshot(self, vehicle_id=LEGACY_VEHICLE) -> dict:
        return self.store.snapshot(vehicle_id)

    def view(self, vehicle_id=LEGACY_VEHICLE) -> dict:
        return self.store.view(vehicle_id)

    def get(self, key: str, default=None, *, vehicle_id=LEGACY_VEHICLE):
        return self.snapshot(vehicle_id).get(key, default)

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception:
                pass
            finally:
                self._nc = None


def _stale_s() -> float:
    try:
        return float(os.getenv("VERIFY_MIRROR_STALE_S", "") or DEFAULT_STALE_S)
    except ValueError:
        return DEFAULT_STALE_S
