"""行程规划 Agent —— 子规划者范本（WS6）。

Phase 1：经 AgentClient 调用导航 Agent 搜 POI，再用 LLM 组织行程。
演示跨 Agent 协作：Planner → trip-planner（子规划者）→ navigation（工具 Agent）。

Phase E 增强：
- 并行调用 info.weather + charging-planner
- NEED_SLOT 追问偏好 + NEED_CONFIRM 确认方案
- trip.modify 意图：LLM 理解 diff → 局部重规划
"""
from __future__ import annotations
import asyncio
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

_SYSTEM = (
    "你是自驾行程规划助手。根据目的地、天数、偏好，以及搜索到的景点信息，"
    "给出简洁的行程建议，按天列要点（每天1-2句），适合语音播报，避免冗长。"
)

_MODIFY_SYSTEM = (
    "你是自驾行程修改助手。根据用户的修改要求和已有行程，给出修改后的行程要点。"
    "只修改用户提到的部分，其他保持不变。适合语音播报。"
)


class TripPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "trip.plan": self._plan,
            "trip.modify": self._modify,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status="failed", speech="行程助手暂不支持该请求。")

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        """规划行程。"""
        dest = intent.slots.get("destination", "")
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您想去哪里玩？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        days = intent.slots.get("days", "")
        prefs = intent.slots.get("preferences", "")

        # 跨 Agent 协作：并行调用导航 + 天气 + 充电
        pois_info = ""
        weather_info = ""
        charging_info = ""
        try:
            results = await asyncio.gather(
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 景点", "rating_min": "4.0"}, ctx),
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 充电桩"}, ctx),
                self.agents.call("info", "info.weather", {"city": dest}, ctx),
                self.agents.call("info", "info.forecast", {"city": dest}, ctx),
                self.agents.call("charging-planner", "charging.plan",
                                 {"destination": dest}, ctx),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, Exception) or r is None:
                    continue
                if not isinstance(r, AgentResult):
                    continue
                if r.ui_card and r.ui_card.get("type") == "poi_list":
                    items = r.ui_card.get("items", [])
                    names = "、".join(i.get("name", "") for i in items[:3])
                    if names:
                        pois_info += f"- {names}\n"
                elif "天气" in (r.speech or "") or "气温" in (r.speech or ""):
                    weather_info = r.speech
                elif "充能" in (r.speech or "") or "充电" in (r.speech or ""):
                    charging_info = r.speech
        except Exception:
            pass  # 协作失败不阻塞，降级为纯 LLM 生成

        # LLM 组织行程
        prompt = (
            f"目的地：{dest}；天数：{days or '不限'}；偏好：{prefs or '无特别要求'}。\n"
            f"原始需求：{intent.raw_text}\n"
        )
        if pois_info:
            prompt += f"\n参考景点/充电信息：\n{pois_info}"
        if weather_info:
            prompt += f"\n天气信息：{weather_info}\n"
        if charging_info:
            prompt += f"\n充能建议：{charging_info}\n"

        plan = await self.llm.complete([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ], temperature=0.7, max_tokens=400)

        # NEED_CONFIRM 确认方案
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{plan}\n\n确认按此方案出行吗？",
            ui_card={"type": "trip_plan", "destination": dest, "days": days,
                     "pois": pois_info.strip(), "weather": weather_info,
                     "charging": charging_info},
            follow_up="说『确认』即可，或告诉我需要调整的地方",
        ).action("trip.plan", {"destination": dest, "days": days}, require_confirm=True)

    async def _modify(self, intent, ctx, meta) -> AgentResult:
        """修改已有行程。"""
        modification = intent.slots.get("modification", "").strip()
        if not modification:
            return AgentResult(
                status=NEED_SLOT, speech="您想怎么调整行程？",
                follow_up="例如：第二天换成宋城", missing_slots=["modification"])

        # LLM 理解 diff 并局部重规划
        prompt = (
            f"用户想修改行程：{modification}\n"
            f"原始需求：{intent.raw_text}\n"
            "请根据修改要求，给出修改后的行程要点（只修改提到的部分）。"
        )
        try:
            modified = await self.llm.complete([
                {"role": "system", "content": _MODIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ], temperature=0.7, max_tokens=300)
        except Exception:
            modified = f"已记录您的修改要求：{modification}。请稍后确认。"

        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{modified}\n\n确认按此调整吗？",
            follow_up="说『确认』即可",
        ).action("trip.modify", {"modification": modification}, require_confirm=True)
