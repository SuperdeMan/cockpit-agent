"""云侧车况镜像（只读）：订阅 NATS `vehicle.state.changed`，供 Outcome Verifier 的
`state_match` 求值器对账（M2 P1）。

**RFC 事实勘误**（`2026-07-25-m2-...-rfc.md` §3.3 写「engine 已持有 VAL 镜像/NATS 镜像」
——不成立）：编排器侧此前只有 NATS **出站**（obs 事件发布），没有任何订阅；`ctx.fetch
("vehicle_state")` 也拿不到车况（memory 里根本没这个 scope，manifest 的 `context_scopes:
[vehicle_state]` 只控制 `vehicle_battery` 一个 meta 键是否下发，见 clients._SENSITIVE_SCOPE）。
车况的真相源是端侧 VAL 经 NATS 广播——`orchestrator/edge/server.py` 每次车控变更发增量 diff、
每 `OBS_SNAPSHOT_INTERVAL`（默认 30s）发一次全量快照。

故本模块按仓库里已跑通三次的同一形态新建（`gateway/edge` 的 vehState、
`observability/collector` 的 CollectorStore、`agents/scene_orchestrator` 的 StateMirror）：
**一条只读订阅、进程内 dict、全程 fail-open**。nats-py 已在 cloud requirements 里
（obs 事件出口用），不引入新依赖、不建新通道。

fail-open 铁律：无 NATS_URL / 连接失败 / 冷启动没收到过快照 → 镜像恒空 →
求值器判 UNKNOWN → **不定罪**。对账是增强，绝不因它误报或阻塞主链。

PoC 单车：与上述三处先例一致，镜像不分 vehicle_id（`vehicle.state.changed` 载荷本身也不带）。
多车时按 vehicle_id 分片是本模块的局部改动，不影响 verifier 接口。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

logger = logging.getLogger("planner.state_mirror")

STATE_SUBJECT = "vehicle.state.changed"
# 镜像陈旧上限：超过这么久没收到任何广播 = 链路断了（edge 每 30s 必发全量快照），
# 此时镜像内容不可信 → 当作"看不见"（UNKNOWN），而不是拿陈旧值去定罪。
DEFAULT_STALE_S = 180.0


class VehicleStateMirror:
    def __init__(self, stale_s: float | None = None):
        self._state: dict = {}
        self._nc = None
        self._updated_at = 0.0
        self._stale_s = stale_s if stale_s is not None else _stale_s()

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
        try:
            event = json.loads(msg.data.decode())
        except Exception:
            return
        changes = [c for c in (event.get("changes") or [])
                   if isinstance(c, dict) and c.get("key")]
        if not changes:
            return
        for c in changes:
            self._state[c["key"]] = c.get("new")
        self._updated_at = time.time()

    def snapshot(self) -> dict:
        """当前车况。无数据 / 陈旧 → 空 dict（求值器据此判 UNKNOWN，不定罪）。"""
        if not self._state:
            return {}
        if self._stale_s and (time.time() - self._updated_at) > self._stale_s:
            return {}
        return dict(self._state)

    def get(self, key: str, default=None):
        return self.snapshot().get(key, default)

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
