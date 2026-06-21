"""点餐 Agent —— 交易类生态 Agent 范本。

演示：第三方信任级、支付权限、二次确认(NEED_CONFIRM)。
Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM, FAILED
from agents._sdk.location import current_location_from_meta
from .providers import build_restaurant_provider

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class FoodOrderingAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.restaurant = build_restaurant_provider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if intent.name == "food.search_restaurant":
            return await self._search(intent, ctx, meta)
        if intent.name == "food.reserve":
            return await self._reserve(intent, meta)
        return AgentResult(status=FAILED, speech="点餐助手暂不支持该请求。")

    async def _search(self, intent, ctx, meta: dict) -> AgentResult:
        cuisine = intent.slots.get("cuisine") or intent.slots.get("keyword") or "美食"
        await ctx.fetch("profile.taste")  # 按引用取口味画像
        rating_min = float(intent.slots.get("rating_min", 0) or 0)
        location = (intent.slots.get("location") or "").strip()
        current = current_location_from_meta(meta)
        if not location and current:
            location = f"{current.lng:.6f},{current.lat:.6f}"
        results = await self.restaurant.search(
            cuisine=cuisine, location=location, rating_min=rating_min)
        names = "、".join(r.name for r in results[:3])
        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "price_per_person": r.price_per_person} for r in results]
        return AgentResult(
            speech=f"为您找到 {len(results)} 家{cuisine}，推荐：{names}。需要订位吗？",
            ui_card={"type": "restaurant_list", "cuisine": cuisine, "items": items},
            follow_up="可以说『订第一家今晚7点两位』",
        )

    async def _reserve(self, intent, meta: dict) -> AgentResult:
        name = intent.slots.get("restaurant_name") or intent.slots.get("restaurant_id")
        if not name:
            return AgentResult(status=NEED_SLOT, speech="您想订哪一家？", follow_up="请告诉我餐厅")
        when = intent.slots.get("datetime", "")
        party = intent.slots.get("party_size", "")
        detail = " ".join(x for x in [name, when, f"{party}位" if party else ""] if x)

        # 用户已二次确认（编排器只对挂起那一步注入 confirmed）→ 真正下单
        if meta.get("confirmed") == "true":
            party_n = int(party) if str(party).isdigit() else 2
            ok, info = await self.restaurant.reserve(name, when, party_n)
            if not ok:
                return AgentResult(status=FAILED, speech=f"预订没有成功：{info}")
            return AgentResult(
                speech=f"已为您订好：{detail}。",
                ui_card={"type": "reservation", "detail": info,
                         "restaurant": name, "datetime": when, "party_size": party},
            )

        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"确认为您预订 {detail} 吗？",
            follow_up="说『确认』即可下单",
        ).action("food.reserve", {"restaurant": name, "datetime": when, "party_size": party},
                 require_confirm=True)
