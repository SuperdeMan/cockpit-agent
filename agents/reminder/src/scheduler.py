"""到点触发调度：轮询 claim_due（原子领取）→ 合并成一次 NATS proactive 发布。

不节流（用户显式契约到点必响，与 road-safety 环境播报不同）；同批多条合并播报。
publish 失败仅日志——条目已 fired 不回滚（宁可漏一次播报，不重复轰炸；P1 补投兜底）。
"""
from __future__ import annotations
import asyncio
import logging
import os
import time

from runtime.proactive import P_USER_CONTRACT

from .store import ReminderStore, group_by_owner
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
        fired_ts = int(self._now() * 1000)
        # 全局扫描可以跨 owner 原子领取，但**消费必须先按 OwnerKey 分组**（M-B）。
        # 此前整批共用一条 speech/card 且 `user_id` 取 due[0]——两个人同一秒到点时，
        # 一个人会听到另一个人的提醒，而整条消息还被记在第一个人名下。
        for (uid, occ), group in group_by_owner(due).items():
            payload = self._payload(uid, occ, group, fired_ts)
            titles = [r.title for r in group]
            try:
                await self._publish(payload)
                logger.info("reminder fired x%d owner=%s/%s: %s",
                            len(group), uid, occ, "、".join(titles)[:60])
            except Exception as e:
                logger.warning("reminder proactive publish failed（不回滚 fired）: %s", e)
        # P1a：重复系列触发后滚动到下一次（fired→pending；错过的次数在 next_recur_fire 里跳过）。
        # publish 失败也照滚——系列的"下一次"不因一次投递失败而停摆。
        for r in due:
            if r.recur:
                try:
                    nxt = next_recur_fire(r.recur, r.fire_at, int(self._now()), self._tz)
                    await self._store.roll_recurring(r.user_id, r.id, nxt,
                                                     occupant_id=r.occupant_id)
                except Exception as e:
                    logger.warning("reminder recur roll failed %s: %s", r.id, e)
        return len(due)

    def _payload(self, user_id: str, occupant_id: str, due: list, fired_ts: int) -> dict:
        titles = [r.title for r in due]
        if len(due) == 1:
            speech = f"叮，到点了：{titles[0]}。"
        else:
            head = "、".join(titles[:3]) + ("等" if len(titles) > 3 else "")
            speech = f"有 {len(due)} 条提醒到点了：{head}。"
        cards = [{"type": "reminder_card", "context": "fired",
                  "item": r.to_card_item(tz=self._tz),
                  "actions": [
                      # action 带 pinned owner + 精确 id（M-B）：HMI 点「完成」时固定用
                      # 卡片上的 owner，不拿点击那一刻的声纹身份或标题模糊匹配去猜——
                      # 后者会跨乘员操作到同名的另一条提醒。**pin 只是数据路由，
                      # occupant 不是权限凭据。**
                      {"label": "完成", "send_text": f"完成提醒：{r.title}",
                       "reminder_id": r.id, "owner_occupant_id": r.occupant_id},
                      {"label": "稍后10分钟", "send_text": f"10分钟后再提醒我{r.title}",
                       "reminder_id": r.id, "owner_occupant_id": r.occupant_id},
                  ]} for r in due]
        return {"type": "reminder_fired", "speech": speech,
                "card": cards[0] if len(cards) == 1 else
                {"type": "card_group", "items": cards},
                "agent_id": "reminder", "ts": fired_ts,
                "user_id": user_id, "owner_occupant_id": occupant_id,
                # 用户显式约定 → 治理器免打扰/负荷/频控全豁免（仅参与合并）。
                # 去重键 = 条目 id + 触发时刻：同一次触发重投不说两遍；**必须带触发
                # 时刻**——snooze 保留原条目 id，只按 id 去重会把「过5分钟再叫我」
                # 的第二次触发在治理器 10 分钟去重窗里静默吞掉（验收抓到，直接违反
                # conventions §9.8「绝不静默吞掉用户显式约定的提醒」）。
                "priority": P_USER_CONTRACT,
                "dedup_key": ("reminder.fired|"
                              + ",".join(sorted(r.id for r in due))
                              + f"|{fired_ts}")}

    async def run_forever(self):
        logger.info("reminder scheduler: poll every %.1fs", self._poll_s)
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.warning("reminder scheduler tick error: %s", e)
            await asyncio.sleep(self._poll_s)
