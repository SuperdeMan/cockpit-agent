"""位置提醒的围栏判定（M3 P1）。

订 `vehicle.state.changed` 的 `location`（road-safety / scene 同一条广播），
对 pending 的位置提醒做**边沿判定**：进围栏（arrive）/ 出围栏（leave）各触发一次。

**首次观测只播种、不触发**——这是「到公司提醒我拿文件」的正确语义：
如果创建提醒时人就在公司，首次观测就触发等于立刻响一声，用户要的是**下次到**。
播种后必须看到一次真实的状态翻转才算数（scene triggers 边沿触发的同一条设计）。

PoC 边界（诚实留档）：`location` 今天只能由 debug 通道注入（`orchestrator/edge/server.py`
的 `_DEBUG_KEYS`），没有真实 GPS 流；接上真车 GPS 后本模块零改动——它只消费广播。
"""
from __future__ import annotations

import logging
import time

from .placeparse import ARRIVE, arrived

logger = logging.getLogger("agent.reminder.geofence")


class GeofenceWatcher:
    def __init__(self, store, publish, *, now_fn=time.time, tz=None):
        self._store = store
        self._publish = publish              # async (payload: dict) -> None
        self._now = now_fn
        self._tz = tz
        self._inside: dict[str, bool] = {}   # reminder id -> 上次是否在围栏内（None=未播种）

    async def on_state(self, changes: list, state: dict) -> int:
        location = state.get("location")
        if not isinstance(location, dict):
            return 0
        try:
            pending = await self._store.list_location_pending()
        except Exception as e:
            logger.debug("位置提醒读取失败：%s", e)
            return 0
        live = {r.id for r in pending}
        for gone in [k for k in self._inside if k not in live]:
            self._inside.pop(gone, None)     # 已触发/取消的条目不留残迹

        hits = []
        for r in pending:
            inside = arrived(r.extra or {}, location)
            was = self._inside.get(r.id)
            self._inside[r.id] = inside
            if was is None:                  # 首次观测只播种
                continue
            want = str((r.extra or {}).get("trigger_on") or ARRIVE)
            if (want == ARRIVE and inside and not was) or \
               (want != ARRIVE and was and not inside):
                hits.append(r)
        if not hits:
            return 0

        now = int(self._now())
        claimed = await self._store.claim_location([r.id for r in hits], now)
        if not claimed:
            return 0
        await self._publish(self._payload(claimed))
        logger.info("位置提醒触达 x%d：%s", len(claimed),
                    "、".join(r.title for r in claimed)[:60])
        return len(claimed)

    def _payload(self, items) -> dict:
        def phrase(r):
            place = str((r.extra or {}).get("place") or "")
            verb = "离开" if (r.extra or {}).get("trigger_on") == "leave" else "到"
            return f"{verb}{place}了" if place else "到地方了"

        if len(items) == 1:
            speech = f"{phrase(items[0])}，提醒你：{items[0].title}。"
        else:
            titles = "、".join(r.title for r in items[:3])
            speech = f"{phrase(items[0])}，有 {len(items)} 条提醒：{titles}。"
        cards = [{"type": "reminder_card", "context": "fired",
                  "item": r.to_card_item(tz=self._tz),
                  "actions": [{"label": "完成", "send_text": f"完成提醒：{r.title}"}]}
                 for r in items]
        from runtime.proactive import P_USER_CONTRACT
        fired_ts = int(self._now() * 1000)
        return {"type": "reminder_fired", "speech": speech,
                "card": cards[0] if len(cards) == 1 else
                {"type": "card_group", "items": cards},
                "agent_id": "reminder", "ts": fired_ts,
                "user_id": items[0].user_id,
                # 用户显式约定：治理器只做合并，不做抑制（到地必响，同到点必响）。
                # 去重键带触发时刻（同 scheduler）：改期后重进围栏的再触发不能被
                # 治理器去重窗吞掉——只有同一次触发的重投才该判重。
                "priority": P_USER_CONTRACT,
                "dedup_key": ("reminder.fired|"
                              + ",".join(sorted(r.id for r in items))
                              + f"|{fired_ts}")}
