"""行程规划 Agent —— 子规划者范本（WS6）。

Phase 1：经 AgentClient 调用导航 Agent 搜 POI，再用 LLM 组织行程。
演示跨 Agent 协作：Planner → trip-planner（子规划者）→ navigation（工具 Agent）。
"""
from __future__ import annotations
import asyncio
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM = (
    "你是自驾行程规划助手。根据目的地、天数、偏好，以及搜索到的景点信息，"
    "给出简洁的行程建议，按天列要点（每天1-2句），适合语音播报，避免冗长。"
)


class TripPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        dest = intent.slots.get("destination", "")
        if not dest:
            return AgentResult(status=NEED_SLOT, speech="您想去哪里玩？", follow_up="请告诉我目的地")

        days = intent.slots.get("days", "")
        prefs = intent.slots.get("preferences", "")

        # 跨 Agent 协作：并行调用导航 Agent 搜景点 + 充电桩
        pois_info = ""
        try:
            results = await asyncio.gather(
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 景点", "rating_min": "4.0"}, ctx),
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 充电桩"}, ctx),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, AgentResult) and r.ui_card:
                    items = r.ui_card.get("items", [])
                    names = "、".join(i.get("name", "") for i in items[:3])
                    if names:
                        pois_info += f"- {names}\n"
        except Exception:
            pass  # 协作失败不阻塞，降级为纯 LLM 生成

        # LLM 组织行程
        prompt = (
            f"目的地：{dest}；天数：{days or '不限'}；偏好：{prefs or '无特别要求'}。\n"
            f"原始需求：{intent.raw_text}\n"
        )
        if pois_info:
            prompt += f"\n参考景点/充电信息：\n{pois_info}"

        plan = await self.llm.complete([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ], temperature=0.7, max_tokens=400)

        return AgentResult(
            speech=plan,
            ui_card={"type": "trip_plan", "destination": dest, "days": days,
                     "pois": pois_info.strip()},
            follow_up="需要我帮你导航或订酒店吗？",
        )
