"""车况镜像：订阅 NATS `vehicle.state.changed`，在进程内维护一份全量车况。

与 `agents/scene_orchestrator/src/state_mirror.py` 同款范式（真相源是端侧 VAL，经
`orchestrator/edge/main.py` 的周期全量快照 + 变更 diff 广播）。冷启动最长一个快照周期
（默认 30s）内补齐——这段窗口里 `speed_kmh` 读不到，驾驶负荷闸按设计**放行**
（子 RFC §2.3 闸4：「读不到车速」不是「用户在忙」的证据）。

全程 fail-open：无 NATS_URL / 连接失败 → 镜像恒空，治理器照常裁决（少一道闸而已）。
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("proactive.mirror")

STATE_SUBJECT = "vehicle.state.changed"


class VehicleMirror:
    def __init__(self):
        self._state: dict = {}

    @property
    def state(self) -> dict:
        return self._state

    def snapshot(self) -> dict:
        return dict(self._state)

    def apply(self, raw: bytes) -> None:
        try:
            event = json.loads(raw.decode())
        except Exception:
            return
        for c in event.get("changes") or []:
            if isinstance(c, dict) and c.get("key"):
                self._state[c["key"]] = c.get("new")

    async def subscribe(self, nc) -> bool:
        if nc is None:
            logger.info("车况镜像禁用（无 NATS 连接）——驾驶负荷闸将始终放行")
            return False
        # cb 必须是协程函数本身：nats-py 用 inspect 判定，lambda 返回协程会被拒
        # （"nats: must use coroutine for subscriptions"）——真栈首启才暴露。
        await nc.subscribe(STATE_SUBJECT, cb=self._on_msg)
        logger.info("已订阅 %s，车况镜像开启", STATE_SUBJECT)
        return True

    async def _on_msg(self, msg) -> None:
        self.apply(msg.data)


def nats_url() -> str:
    return os.getenv("NATS_URL", "")
