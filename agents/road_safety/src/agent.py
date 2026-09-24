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
import re
import time

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED, NEED_CONFIRM
from agents._sdk.location import current_location_from_meta
from agents._sdk.provenance import attach
from runtime.safety_signal import (DRIVER_STATE_ADVICE, alert_level,
                                   alert_resolved, alert_signal, driver_state)
from runtime.clock import hour_of as clock_hour
from runtime.proactive import P_CRITICAL, publish_proactive

logger = logging.getLogger("agent.road_safety")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")

# NATS 主题：订阅车辆状态变更
_STATE_SUBJECT = "vehicle.state.changed"

# ── 知识型安全问法（评审三轮追加批 J，2026-09-24；设计 §13）──────────────────────────────
# 问的是「怎么做 / 要注意什么 / 有什么讲究」，要的是通用建议，不是此刻的天气。修前「怎么缓解开车时的疲劳」
# 「开长途前要注意些什么」都落 `_general_advice`，答成当地天气 +「天气状况良好，适合出行」。情境型问法
# （「还能继续开吗」「适合出行吗」）照旧要当前天气 / 告警；状态自述与告警仍排在它前面（确定性结论不交给模型）。
_KNOWLEDGE_ASK_RE = re.compile(
    r"(?:怎么|怎样|如何)(?:才能|才|能|可以|去|来)?(?:缓解|避免|防止|预防|应对|处理|解决|克服|减轻|提神|保持|开|做)"
    r"|怎么办|注意(?:些)?什么|注意事项|有(?:什么|哪些)(?:建议|技巧|办法|方法|讲究|危害|风险|影响|要注意)"
    r"|(?:准备|检查)(?:些)?什么")
_KNOWLEDGE_SYSTEM = (
    "你是专业的驾驶安全顾问。只回答用户问的驾驶安全常识：给出 2-3 条简洁、可执行的建议，口语化、适合语音播报，"
    "总共不超过 100 字。不要编当前的天气、路况或车辆状态，不要声称已经为用户做了任何操作。")
_KNOWLEDGE_FALLBACK = ("通用的安全建议是：保持车距、控制车速；连续开两个小时左右就找服务区歇一歇，"
                       "感到困倦就尽快停车休息。")

# 驾驶员状态与车辆告警判据的**唯一实现**在 `runtime/safety_signal.py`。
# 这里曾经有一份本地副本、manual-rag 有第二份，chitchat 还要第三份——
# 收口发生在第三个消费方出现的**当天**，不是等它错了再收（§4.3 时区族那笔账）。


# ── 批 7 ②：`route` 槽归一（路况查的是哪条路）────────────────────────────────────────
# planner 填的 route 是自由转述：「去宝安机场的路况」「深南大道路况」「高速」「宝安机场」。
# 三种形态各走各的数据源（判据全是封闭虚词 / 道路量词，零 POI 名）：
#   destination —「去 / 到 X（的路况）」或裸地名 ⇒ 当前位置到 X 一路的逐段拥堵；
#   road        — 路 / 大道 / 高速 / 大桥… 结尾或 G4 / S3 编号 ⇒ 按路名查态势；
#   active      — 「路上 / 前面 / 这条路 / 高速（裸）」或空 ⇒ 正在导航的那条路线。
_ROUTE_ACTIVE_WORDS = frozenset({
    "路上", "前面", "前方", "当前", "当前路线", "这条路", "那条路", "沿途", "高速", "高速上",
    "现在的路", "路况", "一路", "路线", "这段路",
})
_ROUTE_DEST_PREFIX_RE = re.compile(r"^(?:前往|开到|开往|去|到|往|回)")
_ROUTE_NOISE_RE = re.compile(
    r"(?:的路况|的路上|的路|这条路|那条路|路上|沿途|一路|方向|路况|堵不堵|堵吗|堵车吗|堵车|"
    r"怎么走|怎么样|怎样|如何|好走吗|顺不顺|通不通|畅通吗)+$")
_ROAD_SUFFIX_RE = re.compile(
    r"(?:高速公路|高速|快速路|快速干道|大道|环线|环路|大桥|隧道|立交|国道|省道|公路|路|街)$")
_ROAD_CODE_RE = re.compile(r"^[GSgs]\d{1,4}$")


def route_target(text: str | None) -> tuple[str, str]:
    """route 槽 / 原话 → ("destination" | "road" | "active", 目标名)。见上方注释。"""
    t = re.sub(r"[\s，,。！？!?、]+", "", (text or "").strip())
    if not t or t in _ROUTE_ACTIVE_WORDS:
        return "active", ""
    had_prefix = bool(_ROUTE_DEST_PREFIX_RE.match(t))
    core = _ROUTE_DEST_PREFIX_RE.sub("", t, count=1)
    core = _ROUTE_NOISE_RE.sub("", core).strip()
    if not core or core in _ROUTE_ACTIVE_WORDS:
        return "active", ""
    if _ROAD_CODE_RE.match(core) or (not had_prefix and _ROAD_SUFFIX_RE.search(core)):
        return "road", core.upper() if _ROAD_CODE_RE.match(core) else core
    return "destination", core


def _focus_safety_alert(meta) -> dict:
    """从编排下发的 `meta.focus_safety_alert` 读会话告警。解析失败一律当没有
    ——**宁可少一层约束，也不要按一个解析错的等级去劝阻用户**。"""
    raw = (meta or {}).get("focus_safety_alert") if hasattr(meta, "get") else None
    if not raw:
        return {}
    try:
        alert = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    if not isinstance(alert, dict) or alert.get("level") not in ("critical", "amber"):
        return {}
    return alert


_driver_state = driver_state          # 对外名字不变，实现指向 runtime 的唯一份


def _spoken_alert(intent) -> dict:
    """**本轮原话**里的车辆告警（C1-B，2026-08-26 QA P0-01）。

    为什么不能只读会话存储：`_focus_safety_alert` 读的是编排下发的会话态，
    而会话态的唯一写入通道曾是「某个 Agent 恰好在 data 里声明了 `_safety_alert`」。
    info persona 整场没有 manual 轮 ⇒ 存储为空 ⇒「红色机油灯亮了还能继续开吗」
    一路落到**天气建议**分支，答了一段雨天注意事项（T24-25 实录）。

    判据：**本轮原话优先于会话存储**。用户此刻正在说的告警，比会话里存着的那条更新，
    也更不可能是别的意思。零 LLM、纯词表，与走了哪条路由无关。
    """
    text = getattr(intent, "raw_text", "") or ""
    level = alert_level(text)
    if not level:
        return {}
    return {"level": level, "signal": alert_signal(text) or "车辆告警",
            "ts": int(time.time()), "spoken_this_turn": True}


class RoadSafetyAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 主动播报：NATS 连接 + 同类提示节流时间戳
        self._nc = None
        self._last_broadcast: dict[str, float] = {}
        self._last_city = ""          # 上一次已评估的位置（周期全量快照不算「进入新区域」）
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
        """车辆状态变更回调：location **真的变了**才算进入新区域 → 查预警 → 节流后主动播报。

        为什么要比对上一次：`orchestrator/edge/main.py` 每 `OBS_SNAPSHOT_INTERVAL`
        （默认 30s）发一次**全量快照**，快照里 location 一定在 changes 里——不比对的话
        车停着不动也每 30 秒查一次预警。2026-07-25 真栈实测的后果是：一个查不到的地名
        每 30 秒打一次和风 400，把**共享的 qweather 熔断器打开**，天气域跟着一起垮
        （journeys B1-4/B3-4 因此变红）。语义上「进入新区域」本来就该是变沿。
        """
        try:
            event = json.loads(msg.data.decode())
        except Exception:
            return
        city = self._location_from_changes(event.get("changes") or [])
        if not city or city == self._last_city:
            return
        self._last_city = city
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
        # 业务时区（容器 TZ=UTC）：裸 localtime 会把北京 06:00–14:00 当成夜间，
        # 夜间节流窗口整段用错。
        hour = clock_hour(now)
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
        """向主动治理器发安全播报。

        **`critical` 档**：免打扰/驾驶负荷/频控全豁免，合并窗口为 0 立即发——
        危险天气正是开车时该说的话，攒着说等于不说。上面的进程内 30/60 分钟节流保留：
        生产侧防抖与中央治理是两层，不互斥（中央治理管的是跨生产方那一半）。
        """
        if not self._nc:
            return
        payload = {
            "type": advisory_type,
            "speech": speech,
            "agent_id": self.manifest.agent_id,
            "ts": int(time.time() * 1000),
            "priority": P_CRITICAL,
            "dedup_key": f"road-safety.{advisory_type}",
        }
        await publish_proactive(self._nc, payload)
        logger.info("road-safety: 主动播报 %s", speech[:40])

    # ── 请求-响应意图 ────────────────────────────────────────

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "safety.driving_advice": self._driving_advice,
            "safety.driver_state": self._driver_state_intent,
            "safety.weather_alert": self._weather_alert,
            "safety.road_condition": self._road_condition,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="安全助手暂不支持该请求。")

    async def _driving_advice(self, intent, ctx, meta) -> AgentResult:
        """综合天气+路况给出驾驶安全建议。"""
        # ── 驾驶员状态优先（Q9 / QA 轮 I-043）────────────────────────────
        # 在天气/路况之前判。原实现里「驾驶员状态」**根本不是一个输入维度**：
        # `_general_advice` 只看天气现象，于是「困到睁不开眼」得到的回答是
        # 「天气状况良好，适合出行」（迷你集 SF4，--repeat 3 下 0/3 稳定红）。
        # 确定性、零 LLM——**安全结论不该取决于这次模型怎么想**。
        state = _driver_state(intent.raw_text or "")
        if state:
            return self._driver_state_advice(state)

        # ── 本轮原话里就有告警 → 直接按告警答（C1-B）────────────────────
        # 排在会话存储**之前**：这一句是用户此刻说的，比存着的那条更新。
        spoken = _spoken_alert(intent)
        if spoken:
            return self._alert_bound_advice(spoken)

        # ── 会话已有未解除的安全告警 → 不许按天气答（Q9 / QA 轮 I-054）────
        # SF3 实测：红色机油灯之后一句「现在在高速还能继续开吗」被答成
        # 「天气状况良好，适合出行」。`_general_advice` 只看天气现象，
        # **它不知道这个会话里刚响过一个红灯**。告警经 `meta.focus_safety_alert`
        # 由编排广播下来（不按 scope 门控——告警是约束不是敏感数据）。
        # QA T47 裁决 A（2026-09-19）：用户这一句已经说了「机油灯灭了 / 处理好了」，会话里那条
        # 旧告警不再是这轮回答的前提——否则「机油灯灭了，现在还能继续开吗」会被答成
        # 「您这次会话里还有未解除的机油灯」。判据同 `runtime.safety_signal.alert_resolved`
        # （编排同一轮也据此清焦点；这里只是让**这一轮**不再读到它）。
        alert = {} if alert_resolved(intent.raw_text or "") else _focus_safety_alert(meta)
        if alert:
            return self._alert_bound_advice(alert)

        dest = intent.slots.get("destination", "").strip()
        if not dest:
            # badcase 11db5215：「今天天气怎么样，适合出行吗」这类泛出行询问被规划到
            # 本能力时，反问「您要去哪里？」会在多步 plan 里吞掉并行天气步的答案。
            # 无目的地 → 按当前位置天气给一般性出行建议（不追问）；真要路线级建议的
            # 用户会带目的地（「开车去上海安全吗」走下方原逻辑）。
            # 追加批 J：知识型问法（「开长途前要注意些什么」）按问题答，不报天气。
            if _KNOWLEDGE_ASK_RE.search(intent.raw_text or ""):
                return await self._knowledge_advice(intent.raw_text or "")
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

    def _alert_bound_advice(self, alert: dict) -> AgentResult:
        """有未解除告警时的驾驶建议：结论由告警等级定，**不看天气**。

        开场白分两种：告警是**本轮说出来的**时就不能说「您这次会话里还有未解除的…」
        ——那句话在用户刚说出口的那一轮听起来像系统在翻旧账。措辞分开，结论同一条。
        """
        critical = alert.get("level") == "critical"
        sig = alert.get("signal") or "车辆告警"
        # 「亮起」只对灯成立。`alert_signal` 也会取到现象词（漏气/异响/过热），
        # 2026-09-19 起续驾问句的 hint 把这些形态都路由到这里——原句式会念成
        # 「漏气亮起时不要大意」。现象词用「出现…时」。
        if alert.get("spoken_this_turn"):
            opening = (f"{sig}亮起时不要大意。" if sig.endswith("灯")
                       else f"出现{sig}时不要大意。")
        else:
            opening = f"您这次会话里还有未解除的{sig}。"
        speech = (opening
                  + ("在它排除之前不建议继续行驶——请尽快在安全位置靠边停车、熄火，"
                     "并联系救援或前往就近服务点检查。"
                     if critical else
                     "请降低车速、避免长时间或高速行驶，尽快就近检查处理。"))
        return AgentResult(
            speech=speech,
            # `_safety_alert` 保留键：本轮原话扫出来的告警也要**登记进会话态**，
            # 否则下一轮换个 handler 又回到「存储为空 ⇒ 答天气」（C1-B 的另一半）。
            data={"safety_alert_bound": True, "level": alert.get("level"),
                  "_safety_alert": {"level": alert.get("level"), "signal": sig,
                                    "ts": int(alert.get("ts") or time.time())}},
            ui_card=attach({"type": "safety_advice", "advice": speech,
                            "alert": sig}, "road-safety", mode="deterministic",
                           note="按会话未解除告警给出，未经模型生成"),
            follow_up="需要我帮您找最近的服务点吗？")

    async def _driver_state_intent(self, intent, ctx, meta) -> AgentResult:
        """`safety.driver_state` 入口。

        ⚠ **词表认不出时绝不回落到某一档**。首版写的是 `... or "fatigue"`，
        真栈当场兑现成缺陷：planner 把「慢一点开可以吗」也路由到这条 intent，
        于是用户听到「您现在的状态不适合继续开——**困倦时**的反应时间和酒后接近」
        ——**系统声称了一件用户根本没说的事**（同 nearby 那几例假个性化）。
        认不出就退回通用安全建议路径，让下游按会话告警/天气路况正常回答。
        """
        state = driver_state(intent.raw_text or "")
        if state:
            return self._driver_state_advice(state)
        # 同 `_driving_advice`：本轮原话优先于会话存储（C1-B）。两条入口都要有，
        # 因为 planner 把「X灯亮了还能开吗」路由到哪一条是有方差的
        # ——**同一个事实不该取决于这次落到了哪个 handler**。
        spoken = _spoken_alert(intent)
        if spoken:
            return self._alert_bound_advice(spoken)
        alert = _focus_safety_alert(meta)
        if alert:
            return self._alert_bound_advice(alert)
        # 追加批 J：「怎么缓解开车时的疲劳」问的是方法、不是自述——按问题答，不报天气。
        if _KNOWLEDGE_ASK_RE.search(intent.raw_text or ""):
            return await self._knowledge_advice(intent.raw_text or "")
        return await self._general_advice(ctx, meta)

    async def _knowledge_advice(self, question: str) -> AgentResult:
        """知识型安全问句（追加批 J）：按问题给通用驾驶安全建议，不报此刻的天气；模型失败回落一句固定建议。"""
        try:
            advice = str(await self.llm.complete([
                {"role": "system", "content": _KNOWLEDGE_SYSTEM},
                {"role": "user", "content": f"用户问：{question}"},
            ], temperature=0.3, max_tokens=200) or "").strip()
        except Exception as e:
            logger.debug("road-safety: knowledge advice failed: %s", e)
            advice = ""
        advice = advice or _KNOWLEDGE_FALLBACK
        return AgentResult(speech=advice, ui_card={"type": "safety_advice", "advice": advice})

    def _driver_state_advice(self, state: str) -> AgentResult:
        """驾驶员状态的**确定性**安全结论。不调 LLM、不看天气。

        同时经保留键 `_safety_alert` 声明会话态——否则下一轮「别提醒我，继续开就行」
        没有任何东西挡得住（QA 实测那轮的回答是「收到，那不提醒也不停车」）。
        """
        spec = DRIVER_STATE_ADVICE[state]
        return AgentResult(
            speech=spec["speech"],
            data={"driver_state": state,
                  "_safety_alert": {"level": spec["level"], "signal": spec["signal"]}},
            ui_card=attach({"type": "safety_advice", "driver_state": state,
                            "advice": spec["speech"]},
                           "road-safety", mode="deterministic",
                           note="确定性安全判据，未经模型生成"),
            follow_up=spec["follow_up"],
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
        """查询天气预警。

        ⚠ **不回退 `vehicle.location`**（C9-A，2026-08-28）：那是 `memory/store.py`
        的 mock 车辆位置（`{"city":"上海","road":"延安高架"}`），本方法是全仓**最后一条**
        还留着这条回退的天气路径——info 的 `_resolve_city` 与 navigation 的
        `current_location_from_meta` 早已显式移除并留了注释。真栈实测（2026-08-26 QA
        info T4）：深圳三轮之后这一句答出「上海当前有1条天气预警」，且 T5 的模型
        **从这句答案里学会了上海**，污染自我延续。
        判据同 §9.5 铁律③：**宁可诚实问一句，不拿 mock 冒充真实位置**。
        定位来源与 info 同源——本轮 GPS（`current_location_from_meta`）→ 坐标串交给
        下游 provider 反查；再没有就 NEED_SLOT。
        """
        city = intent.slots.get("city", "").strip()
        # **查询用的定位串** 与 **说给人听的地名** 是两个东西（2026-08-28 修）。
        # C9-A 把这条路径从「mock 城市名」换成了 `lng,lat` 坐标串交给 provider，
        # 而下面那句兜底话术还在直接念 `city` ⇒ 真栈实测用户听到
        # 「**113.941200,22.541000**当前没有生效的天气预警。」
        # C9-D 在 info 那侧挡住了同一个形态（`_display_city` / `_is_coordinate_label`），
        # **偏偏漏了 C9-A 自己改的这个文件**——新造一条数据通路时，
        # 要把它流向的**每一个出口**都走一遍，不能只修被点名的那几个。
        spoken = city
        if not city:
            current = current_location_from_meta(meta)
            if current:
                # 和风 GeoAPI 接受 `lng,lat`（与 info `_resolve_city` 逐字同格）。
                city = f"{current.lng:.6f},{current.lat:.6f}"
                # 用户没点名城市 ⇒ 话术里只说「当前位置」，坐标不进耳朵。
                spoken = "当前位置"
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

        return AgentResult(speech=f"{spoken}当前没有生效的天气预警。")

    #: 拥堵 + 严重拥堵合计到这个里程才补安全提示——几百米的堵点不值得唠叨一句。
    _CONGESTION_TIP_KM = 1.0

    async def _road_condition(self, intent, ctx, meta) -> AgentResult:
        """查询路况（批 7 ② 重写）。

        修前拿「X 路况」当关键词搜 POI，空结果原样播「为您找到 0 个X 路况，推荐前三个：。需要导航过去吗？」
        ——名字存在能力不可达。现在：route 槽（空则看原话）归一成 destination / road / active 三种形态，
        交 navigation 的内部意图 `route_traffic`（真数据：路线逐段 tmcs / 路名态势 / 活动路线）；
        话术与卡原样转发，拥堵成规模时补一句安全提示。数据拿不到由对方如实说，这里不编。
        """
        route = (intent.slots.get("route") or "").strip()
        kind, target = route_target(route or getattr(intent, "raw_text", ""))
        slots: dict[str, str] = {}
        if kind == "destination":
            slots["destination"] = target
        elif kind == "road":
            slots["road"] = target
        elif not (meta or {}).get("focus_active_route"):
            # 既没说哪条路，也没有正在导航的路线：照旧反问（本能力自己的槽名）
            return AgentResult(
                status=NEED_SLOT, speech="您想查询哪条路线的路况？",
                follow_up="请告诉我路线或目的地", missing_slots=["route"])
        origin = (intent.slots.get("origin") or "").strip()
        if origin:
            slots["origin"] = origin

        label = target or "这一路"
        if kind == "destination":
            label = f"去{target}这一路"
        try:
            result = await self.agents.call("navigation", "navigation.route_traffic", slots, ctx)
        except Exception as e:
            logger.warning("road condition query failed: %s", e)
            result = None
        if result is None or result.status not in ("ok", "need_slot") or not result.speech:
            return AgentResult(speech=f"暂时查不到{label}的路况。")
        if result.status == "need_slot":
            # 对方要目的地（活动路线过龄等）：挂起落在本能力自己的槽上
            return AgentResult(
                status=NEED_SLOT, speech="您想查询哪条路线的路况？",
                follow_up="请告诉我路线或目的地", missing_slots=["route"])
        speech = result.speech
        traffic = (result.data or {}).get("traffic") if isinstance(result.data, dict) else None
        if isinstance(traffic, dict):
            try:
                jam_km = float(traffic.get("congested_km") or 0) + float(traffic.get("blocked_km") or 0)
            except (TypeError, ValueError):
                jam_km = 0.0
            if jam_km >= self._CONGESTION_TIP_KM:
                speech = speech.rstrip() + "拥堵路段请保持车距、提前变道。"
        return AgentResult(speech=speech, ui_card=result.ui_card, data=result.data)
