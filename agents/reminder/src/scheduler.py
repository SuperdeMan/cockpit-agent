"""到点触发调度：轮询 claim_due（原子领取）→ 合并成一次 NATS proactive 发布。

不节流（用户显式契约到点必响，与 road-safety 环境播报不同）；同批多条合并播报。
publish 失败仅日志——条目已 fired 不回滚（宁可漏一次播报，不重复轰炸；P1 补投兜底）。
"""
from __future__ import annotations
import asyncio
import logging
import os
import time

from .store import ReminderStore
from .timeparse import business_tz, next_recur_fire

logger = logging.getLogger("agent.reminder.scheduler")


class ReminderScheduler:
    def __init__(self, store: ReminderStore, publish, *,
                 poll_s: float | None = None, now_fn=time.time):
        self._store = store
        self._publish = publish          # async callable(payload: dict)
        self._poll_s = poll_s if poll_s is not None else float(os.getenv("REMINDER_POLL_S", "5"))
        self._now = now_fn
        self._tz = business_tz()

    async def tick(self) -> int:
        due = await self._store.claim_due(int(self._now()))
        if not due:
            return 0
        titles = [r.title for r in due]
        if len(due) == 1:
            speech = f"叮，到点了：{titles[0]}。"
        else:
            head = "、".join(titles[:3]) + ("等" if len(titles) > 3 else "")
            speech = f"有 {len(due)} 条提醒到点了：{head}。"
        cards = [{"type": "reminder_card", "context": "fired",
                  "item": r.to_card_item(tz=self._tz),
                  "actions": [
                      {"label": "完成", "send_text": f"完成提醒：{r.title}"},
                      {"label": "稍后10分钟", "send_text": f"10分钟后再提醒我{r.title}"},
                  ]} for r in due]
        payload = {"type": "reminder_fired", "speech": speech,
                   "card": cards[0] if len(cards) == 1 else
                   {"type": "card_group", "items": cards},
                   "agent_id": "reminder", "ts": int(self._now() * 1000),
                   "user_id": due[0].user_id}
        try:
            await self._publish(payload)
            logger.info("reminder fired x%d: %s", len(due), "、".join(titles)[:60])
        except Exception as e:
            logger.warning("reminder proactive publish failed（不回滚 fired）: %s", e)
        # P1a：重复系列触发后滚动到下一次（fired→pending；错过的次数在 next_recur_fire 里跳过）。
        # publish 失败也照滚——系列的"下一次"不因一次投递失败而停摆。
        for r in due:
            if r.recur:
                try:
                    nxt = next_recur_fire(r.recur, r.fire_at, int(self._now()), self._tz)
                    await self._store.roll_recurring(r.user_id, r.id, nxt)
                except Exception as e:
                    logger.warning("reminder recur roll failed %s: %s", r.id, e)
        return len(due)

    async def run_forever(self):
        logger.info("reminder scheduler: poll every %.1fs", self._poll_s)
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.warning("reminder scheduler tick error: %s", e)
            await asyncio.sleep(self._poll_s)
