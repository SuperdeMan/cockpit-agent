"""天气路况安全助手（road-safety）—— Sub-planner + 响应式。

综合天气 + 路况 + 车辆状态 → 安全建议。
只建议，不自动控车；如需控车必须 NEED_CONFIRM。
"""
from __future__ import annotations
import asyncio
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED, NEED_CONFIRM

logger = logging.getLogger("agent.road_safety")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class RoadSafetyAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "safety.driving_advice": self._driving_advice,
            "safety.weather_alert": self._weather_alert,
            "safety.road_condition": self._road_condition,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="安全助手暂不支持该请求。")

    async def _driving_advice(self, intent, ctx, meta) -> AgentResult:
        """综合天气+路况给出驾驶安全建议。"""
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您要去哪里？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        # 并行调用 info.weather + info.forecast + navigation.search_poi
        try:
            results = await asyncio.gather(
                self.agents.call("info", "info.weather", {"city": dest}, ctx),
                self.agents.call("info", "info.forecast", {"city": dest}, ctx),
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 路线"}, ctx),
                return_exceptions=True,
            )
        except Exception:
            results = [None, None, None]

        # 收集结果
        weather_info = ""
        forecast_info = ""
        route_info = ""

        for r in results:
            if isinstance(r, Exception) or r is None:
                continue
            if hasattr(r, "speech") and r.speech:
                # 简单分类
                if "天气" in r.speech or "气温" in r.speech:
                    if not weather_info:
                        weather_info = r.speech
                    else:
                        forecast_info = r.speech
                elif "路线" in r.speech or "导航" in r.speech:
                    route_info = r.speech

        # 读车辆状态
        ctx_values = await ctx.fetch("vehicle.speed", "vehicle.battery")
        speed = ctx_values.get("vehicle.speed", "")
        battery = ctx_values.get("vehicle.battery", "")

        # LLM 综合分析
        prompt = (
            f"目的地：{dest}\n"
            f"天气信息：{weather_info or '暂无'}\n"
            f"天气预报：{forecast_info or '暂无'}\n"
            f"路线信息：{route_info or '暂无'}\n"
            f"当前车速：{speed}，电量：{battery}\n\n"
            "请根据以上信息，给出简洁的驾驶安全建议（2-3句话），适合语音播报。"
        )
        try:
            advice = await self.llm.complete([
                {"role": "system", "content": "你是专业的驾驶安全顾问，只给出安全建议，不直接控制车辆。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3, max_tokens=200)
        except Exception:
            advice = "建议出发前检查天气和路况，保持安全车距。"

        return AgentResult(
            speech=advice,
            ui_card={"type": "safety_advice", "destination": dest,
                     "advice": advice, "weather": weather_info,
                     "route": route_info},
            follow_up="需要帮您打开除雾或导航到服务区吗？",
        )

    async def _weather_alert(self, intent, ctx, meta) -> AgentResult:
        """查询天气预警。"""
        city = intent.slots.get("city", "").strip()
        if not city:
            # 尝试从位置解析
            loc_values = await ctx.fetch("vehicle.location")
            city = loc_values.get("vehicle.location", "")
        if not city:
            return AgentResult(
                status=NEED_SLOT, speech="您想查询哪个城市的天气预警？",
                follow_up="请告诉我城市名", missing_slots=["city"])

        # 调用 info agent 查天气预警
        try:
            result = await self.agents.call(
                "info", "info.alerts", {"city": city}, ctx)
            if result and result.speech:
                return AgentResult(
                    speech=result.speech,
                    ui_card=result.ui_card,
                    data=result.data,
                )
        except Exception as e:
            logger.warning("weather alert query failed: %s", e)

        return AgentResult(speech=f"{city}当前没有生效的天气预警。")

    async def _road_condition(self, intent, ctx, meta) -> AgentResult:
        """查询路况。"""
        route = intent.slots.get("route", "").strip()
        if not route:
            return AgentResult(
                status=NEED_SLOT, speech="您想查询哪条路线的路况？",
                follow_up="请告诉我路线或目的地", missing_slots=["route"])

        # 调用 navigation agent 查路线
        try:
            result = await self.agents.call(
                "navigation", "navigation.search_poi",
                {"keyword": f"{route} 路况"}, ctx)
            if result and result.speech:
                return AgentResult(
                    speech=result.speech,
                    ui_card=result.ui_card,
                    data=result.data,
                )
        except Exception as e:
            logger.warning("road condition query failed: %s", e)

        return AgentResult(speech=f"暂无{route}的实时路况信息。")
