"""主动早报域（P2 雏形，响应式）：晨间起步 → 每日一次新闻速览 → agent.proactive（NATS）。

复用 road-safety on_start→agent.proactive 范式。新闻聚合方法（_gather_news/_dedup_news/
_clean_title）在 NewsMixin，本 mixin 经 self 调用。
"""
from __future__ import annotations
import json
import logging
import os
import time

from ._util import _shanghai_now

logger = logging.getLogger("agent.info")


class BriefingMixin:
    async def on_start(self) -> None:
        """serve() 启动后订阅 NATS vehicle.state.changed；无 NATS_URL/连接失败 → 静默禁用。"""
        nats_url = os.getenv("NATS_URL", "")
        if not nats_url:
            return
        try:
            import nats
            self._nc = await nats.connect(nats_url, max_reconnect_attempts=-1)
        except Exception as e:
            logger.warning("info: NATS 连接失败，主动早报禁用：%s", e)
            return
        await self._nc.subscribe("vehicle.state.changed", cb=self._on_state_event)
        logger.info("info: 已订阅 vehicle.state.changed，开启主动早报雏形")

    async def _on_state_event(self, msg) -> None:
        """晨间（6-10 点）首次行驶（挂挡/起步）→ 每日一次主动播报新闻速览（best-effort）。"""
        try:
            event = json.loads(msg.data.decode())
        except Exception:
            return
        if not self._is_morning_drive(event):
            return
        today = _shanghai_now().strftime("%Y-%m-%d")
        if self._last_briefing_date == today:        # 每日一次
            return
        self._last_briefing_date = today
        await self._publish_morning_briefing()

    @staticmethod
    def _has_drive_start(changes) -> bool:
        """changes 里是否出现「起步」信号（挂挡 D/R/S 或车速>0）。"""
        for c in (changes or []):
            if c.get("key") == "gear" and str(c.get("new")) in ("D", "R", "S"):
                return True
            if c.get("key") in ("speed", "speed_kmh"):
                try:
                    if float(c.get("new") or 0) > 0:
                        return True
                except (TypeError, ValueError):
                    pass
        return False

    @staticmethod
    def _is_morning_drive(event: dict) -> bool:
        return (6 <= _shanghai_now().hour < 10
                and BriefingMixin._has_drive_start(event.get("changes")))

    async def _publish_morning_briefing(self) -> None:
        """聚合 top 新闻 → 发 agent.proactive（edge 网关订阅后广播给 HMI）。"""
        if not self._nc:
            return
        try:
            raw = self._dedup_news(await self._gather_news("", 6, None))[:3]
        except Exception as e:
            logger.debug("info: 早报聚合失败：%s", e)
            return
        if not raw:
            return
        heads = "；".join(f"{i}. {self._clean_title(n['title'])}"
                         for i, n in enumerate(raw, 1))
        speech = f"早安！今天有几条值得关注的新闻——{heads}。说『看新闻』我给你逐条讲。"
        payload = {"type": "morning_news", "speech": speech,
                   "agent_id": self.manifest.agent_id, "ts": int(time.time() * 1000)}
        try:
            await self._nc.publish(
                "agent.proactive",
                json.dumps(payload, ensure_ascii=False).encode())
            logger.info("info: 主动早报 %s", speech[:40])
        except Exception as e:
            logger.debug("info: 主动早报发布失败：%s", e)
