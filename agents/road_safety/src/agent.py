"""天气路况安全助手（road-safety）—— Sub-planner + 响应式。

综合天气 + 路况 + 车辆状态 → 安全建议。
只建议，不自动控车；如需控车必须 NEED_CONFIRM。

响应式主动播报（设计 §3.3 场景2）：on_start() 订阅 NATS vehicle.state.changed，
车辆进入新区域（location 变更）时查天气预警，命中危险天气则节流（默认 30 分钟，
夜间降频 60 分钟）后向 NATS 发主动播报事件 agent.proactive。
交付边界：Proactive 通道帧已在 channel.proto/网关定义，但 NATS→Proactive→HMI 的
投递桥接尚未实现（网关当前仅日志）；本 Agent 负责"产出并发布主动播报"，HMI 投递为后续一跳。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED, NEED_CONFIRM

logger = logging.getLogger("agent.road_safety")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# NATS 主题：订阅车辆状态变更，发布主动播报
_STATE_SUBJECT = "vehicle.state.changed"
_PROACTIVE_SUBJECT = "agent.proactive"


class RoadSafetyAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 主动播报：NATS 连接 + 同类提示节流时间戳
        self._nc = None
        self._last_broadcast: dict[str, float] = {}
        # 节流窗口：同类提示默认 30 分钟不重复；夜间（22:00–06:00）降频到 60 分钟
        self._throttle_sec = float(os.getenv("ROAD_SAFETY_THROTTLE_SEC", "1800"))
        self._night_throttle_sec = float(
            os.getenv("ROAD_SAFETY_NIGHT_THROTTLE_SEC", "3600"))

    # ── 响应式主动播报（设计 §3.3 场景2）────────────────────────

    async def on_start(self) -> None:
        """serve() 启动后订阅 NATS；无 NATS_URL 或连接失败 → 静默禁用，不影响请求-响应服务。"""
        nats_url = os.getenv("NATS_URL", "")
        if not nats_url:
            logger.info("road-safety: NATS_URL 未设置，主动播报禁用")
            return
        try:
            import nats
            self._nc = await nats.connect(nats_url, max_reconnect_attempts=-1)
        except Exception as e:
            logger.warning("road-safety: NATS 连接失败，主动播报禁用：%s", e)
            return
        await self._nc.subscribe(_STATE_SUBJECT, cb=self._on_state_event)
        logger.info("road-safety: 已订阅 %s，开启主动播报", _STATE_SUBJECT)

    async def _on_state_event(self, msg) -> None:
        """车辆状态变更回调：location 变更视为进入新区域 → 查预警 → 节流后主动播报。"""
        try:
            event = json.loads(msg.data.decode())
        except Exception:
            return
        city = self._location_from_changes(event.get("changes") or [])
        if not city:
            return
        advisory = await self._evaluate_hazard(city)
        if advisory:
            await self._maybe_broadcast("weather_safety", "weather_safety", advisory)

    @staticmethod
    def _location_from_changes(changes: list) -> str:
        """从 vehicle.state.changed 的 changes 里取新位置（dict 取 city/name，否则原值）。"""
        for c in changes:
            if c.get("key") == "location" and c.get("new"):
                loc = c["new"]
                if isinstance(loc, dict):
                    return loc.get("city") or loc.get("name") or ""
                return str(loc)
        return ""

    async def _evaluate_hazard(self, city: str) -> str | None:
        """查 info.alerts；有生效预警 → 返回主动播报话术，否则 None。

        PoC 判据：info.alerts 有预警时话术含「N 条天气预警」，无预警话术不含——
        以此区分，避免依赖跨进程结构化 data（AgentClient 当前不透传 data 字段）。
        """
        if not city:
            return None
        try:
            res = await self.agents.call("info", "info.alerts", {"city": city}, ctx=None)
        except Exception as e:
            logger.debug("road-safety: 预警查询失败：%s", e)
            return None
        if not res or res.status != "ok" or not res.speech:
            return None
        if "条天气预警" not in res.speech:
            return None
        return f"{res.speech}建议降低车速、保持车距，必要时就近选择服务区休息。"

    def _is_night(self, now: float) -> bool:
        hour = time.localtime(now).tm_hour
        return hour >= 22 or hour < 6

    def _should_broadcast(self, category: str, now: float) -> bool:
        """同类提示节流：距上次播报不足窗口（夜间用更长窗口）→ 抑制。"""
        window = self._night_throttle_sec if self._is_night(now) else self._throttle_sec
        last = self._last_broadcast.get(category)
        return last is None or (now - last) >= window

    async def _maybe_broadcast(
            self, category: str, advisory_type: str, speech: str) -> bool:
        """节流通过则记录时间戳并发布主动播报事件；被节流返回 False。"""
        now = time.time()
        if not self._should_broadcast(category, now):
            logger.debug("road-safety: 「%s」处于节流窗口内，跳过", category)
            return False
        self._last_broadcast[category] = now
        await self._publish_proactive(advisory_type, speech)
        return True

    async def _publish_proactive(self, advisory_type: str, speech: str) -> None:
        """向 NATS 发主动播报事件（best-effort）。HMI 投递桥接为后续一跳。"""
        if not self._nc:
            return
        payload = {
            "type": advisory_type,
            "speech": speech,
            "agent_id": self.manifest.agent_id,
            "ts": int(time.time() * 1000),
        }
        try:
            await self._nc.publish(
                _PROACTIVE_SUBJECT,
                json.dumps(payload, ensure_ascii=False).encode(),
            )
            logger.info("road-safety: 主动播报 %s", speech[:40])
        except Exception as e:
            logger.debug("road-safety: 主动播报发布失败：%s", e)

    # ── 请求-响应意图 ────────────────────────────────────────

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
            # badcase 11db5215：「今天天气怎么样，适合出行吗」这类泛出行询问被规划到
            # 本能力时，反问「您要去哪里？」会在多步 plan 里吞掉并行天气步的答案。
            # 无目的地 → 按当前位置天气给一般性出行建议（不追问）；真要路线级建议的
            # 用户会带目的地（「开车去上海安全吗」走下方原逻辑）。
            return await self._general_advice(ctx, meta)

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

    async def _general_advice(self, ctx, meta) -> AgentResult:
        """无目的地的一般性出行建议：当前位置天气实况 + 按天气现象的确定性驾驶提示
        （零 LLM——泛询问要快、要稳，路线级建议才走 LLM 综合）。"""
        weather = ""
        card = None
        try:
            res = await self.agents.call("info", "info.weather", {}, ctx)
            if res is not None and res.status == "ok" and res.speech:
                weather = res.speech.strip()
                card = res.ui_card or None
        except Exception as e:
            logger.debug("road-safety: general advice weather query failed: %s", e)
        if "雨" in weather:
            tip = "有降雨，路面湿滑，建议减速慢行、保持车距。"
        elif "雪" in weather:
            tip = "有降雪，注意防滑，缓加速、缓刹车。"
        elif "雾" in weather or "霾" in weather:
            tip = "能见度可能受限，请打开雾灯、控制车速。"
        elif weather:
            tip = "天气状况良好，适合出行，注意劳逸结合。"
        else:
            tip = "暂时没拿到天气实况，出行请减速慢行、保持车距。"
        speech = f"{weather}{tip}" if weather else tip
        return AgentResult(speech=speech, ui_card=card,
                           follow_up="需要我按目的地给更具体的路线建议吗？")

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
