"""车辆状态镜像：订阅 NATS `vehicle.state.changed`，在进程内维护一份全量车况。

**为什么不用 `ctx.fetch("vehicle_state")`**：memory 里根本没有这个 scope——manifest 的
`context_scopes: [vehicle_state]` 只控制一个 meta 键（`vehicle_battery`）是否下发，见
`orchestrator/cloud/clients.py::_SENSITIVE_SCOPE`。车况的真相源是端侧 VAL，经 NATS 广播：
`orchestrator/edge/main.py` 每 `OBS_SNAPSHOT_INTERVAL`（默认 30s）发一次**全量快照**、
每次车控变更发**增量 diff**（`drain_state`）。gateway/edge 的 `vehState` 与
observability/collector 的 `CollectorStore` 都是这么建镜像的，本模块同款。

**一条订阅、多消费方**（设计 §7.2）：P0 只用镜像本身（deactivate 的激活前快照）；
P2 的 Verify 对账、P3 的事件触发与驻车补做经 `on_change()` 挂回调，**不新建订阅**。

冷启动：进程刚起时镜像为空，最多一个快照周期（30s）内补齐。全程 fail-open——
拿不到状态就当"读不到"，由调用方退反向默认表/跳过，绝不阻塞主链路。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Awaitable, Callable

logger = logging.getLogger("agent.scene.mirror")

STATE_SUBJECT = "vehicle.state.changed"
PROACTIVE_SUBJECT = "agent.proactive"

ChangeCb = Callable[[list, dict], Awaitable[None]]


class StateMirror:
    def __init__(self):
        self._state: dict = {}
        self._nc = None
        self._cbs: list[ChangeCb] = []

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

    def on_change(self, cb: ChangeCb) -> None:
        """挂一个变更消费方：cb(changes, full_state)。异常由本模块吞掉（fail-open）。"""
        self._cbs.append(cb)

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
        for cb in list(self._cbs):
            try:
                await cb(changes, dict(self._state))
            except asyncio.CancelledError:
                raise
            except Exception as e:                 # 一个消费方炸了不能拖垮镜像
                logger.warning("scene: 状态消费方异常（忽略）：%s", e)

    # ── 读 ──
    def snapshot(self) -> dict:
        return dict(self._state)

    def get(self, key: str, default=None):
        return self._state.get(key, default)

    def capture(self, keys) -> dict:
        """按键取快照；**读不到的键记 None**（调用方据此退反向默认表，D5）。"""
        return {k: self._state.get(k) for k in keys}

    # ── 写（proactive 播报；P2 Verify / P3 触发用）──
    async def publish(self, payload: dict, subject: str = PROACTIVE_SUBJECT) -> bool:
        if not self._nc:
            logger.info("scene: NATS 未连接，proactive 未推送：%s",
                        str(payload.get("speech", ""))[:40])
            return False
        try:
            await self._nc.publish(
                subject, json.dumps(payload, ensure_ascii=False).encode())
            return True
        except Exception as e:
            logger.warning("scene: proactive 推送失败：%s", e)
            return False

    async def close(self) -> None:
        if self._nc:
            try:
                await self._nc.close()
            finally:
                self._nc = None
