"""充能规划 Agent（charging-planner）—— Leaf 工具型范本。

帮用户找充电桩、根据电量/续航推荐、规划长途充能策略。
不做车控——只产出导航动作和信息建议。
"""
from __future__ import annotations
from runtime.vehicle_state import VehicleStateStore
import json
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from agents._sdk.location import current_location_from_meta
from agents._sdk.provenance import attach
from agents._sdk.landmark import is_landmark_description, landmark_candidates
from agents._sdk.shared_state import CHARGING_DEST_CHOICES, CHARGING_RECOMMENDED
from agents._sdk.dest_choice import resolve_ordinal, save_choices
from runtime.proactive import publish_proactive
from runtime import vehicle_reading
from runtime.vehicle_reading import Reading
from runtime.charger_swap import asks_to_swap_charger
from .low_battery import LowBatteryWatcher
from .providers import build_charging_provider
from .providers.base import GeoPoint

logger = logging.getLogger("agent.charging_planner")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class ChargingPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.charging = build_charging_provider()
        self._nc = None
        self._vehicle_states = VehicleStateStore()
        self._low_batteries = {}
        self._state: dict = {}
        self._low_battery = None

    # ── 低电量主动建议（M3 P0）────────────────────────────────────────────
    async def on_start(self) -> None:
        """订车况广播，电量跌破阈值的变沿发一条建议（road-safety 同款范式）。

        无 NATS_URL / 连接失败 → 静默禁用，不影响请求-响应服务。
        """
        url = os.getenv("NATS_URL", "")
        if not url:
            logger.info("charging: NATS_URL 未设置，低电量主动建议禁用")
            return
        try:
            import nats
            self._nc = await nats.connect(url, max_reconnect_attempts=-1)
        except Exception as e:
            logger.warning("charging: NATS 连接失败，低电量主动建议禁用：%s", e)
            return
        self._low_battery = LowBatteryWatcher(
            self._publish_proactive, self._find_stations_for_advice,
            threshold=float(os.getenv("CHARGING_LOW_SOC", "20")),
            throttle_s=float(os.getenv("CHARGING_LOW_SOC_THROTTLE_S", "1800")),
            agent_id=self.manifest.agent_id, attach_prov=self._attach_station_prov)
        await self._nc.subscribe("vehicle.state.changed", cb=self._on_state_event)
        logger.info("charging: 已订阅车况，低电量主动建议开启（阈值 %s%%）",
                    os.getenv("CHARGING_LOW_SOC", "20"))

    async def _on_state_event(self, msg) -> None:
        result = self._vehicle_states.ingest(msg.data)
        if not result.accepted or not self._low_battery:
            return
        # CA2-19 S1：用 view 而不是 snapshot——同一份车况连同每个信号的时效与来源
        view = self._vehicle_states.view(result.vehicle_id)
        state = view["state"]
        battery = vehicle_reading.from_view(view, "battery")
        if result.vehicle_id == "v1":
            self._state = state
            watcher = self._low_battery
        else:
            watcher = self._low_batteries.get(result.vehicle_id)
            if watcher is None:
                watcher = self._low_batteries[result.vehicle_id] = LowBatteryWatcher(
                    self._publish_proactive, self._find_stations_for_advice,
                    threshold=float(os.getenv("CHARGING_LOW_SOC", "20")),
                    throttle_s=float(os.getenv("CHARGING_LOW_SOC_THROTTLE_S", "1800")),
                    agent_id=self.manifest.agent_id, vehicle_id=result.vehicle_id,
                    attach_prov=self._attach_station_prov)
        try:
            await watcher.on_state(list(result.changes), state, battery)
        except Exception as e:
            logger.warning("charging: 低电量建议异常（忽略）：%s", e)

    async def _find_stations_for_advice(self, point):
        return await self.charging.find_nearby(point)

    def _attach_station_prov(self, card: dict) -> dict:
        return attach(card, self.charging)

    async def _publish_proactive(self, payload: dict) -> None:
        await publish_proactive(self._nc, payload)

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "charging.find": self._find,
            "charging.plan": self._plan,
            "charging.status": self._status,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="充能助手暂不支持该请求。")

    @staticmethod
    def _route_has_stops(meta) -> bool:
        """活动路线（编排按 location 范围下发的服务端事实）上有没有途经点。"""
        try:
            route = json.loads((meta or {}).get("focus_active_route") or "")
        except (TypeError, ValueError):
            return False
        return isinstance(route, dict) and bool(route.get("waypoints"))

    async def _resolve_soc(self, ctx, meta) -> Reading:
        """当前电量连同时效与来源（CA2-19 S1）：只认编排下发、验签且未过期的车况读数。

        读不到 ⇒ `value is None`，调用方如实说没读到、不判断够不够，**不拿任何缺省值代替**（此前按 50% 算，
        会说出「当前电量50%足够直达」这种编出来的车况）。直接给了裸 `vehicle_battery` 的只有测试与旧通道
        （生产由权限视图从同一份车况派生），来源不明、不加说明。
        """
        reading = ctx.vehicle_reading("battery") if hasattr(ctx, "vehicle_reading") else Reading("battery")
        if reading.known:
            return reading
        raw = str((meta or {}).get("vehicle_battery", "") or "").strip()
        return Reading("battery", raw) if raw else reading

    @staticmethod
    def _soc_card(reading: Reading) -> dict:
        """卡片上的电量：读到才给 `soc`（客户端据此画电量条），来源 / 说明一律带上。"""
        pct = reading.percent()
        return {"soc": f"{pct}%" if pct is not None else "", **reading.card_fields("soc")}

    async def _find(self, intent, ctx, meta) -> AgentResult:
        """找附近的充电站。带 destination 槽位时按目的地搜，最优站作为导航途经点。"""
        # CA2-19 换站：正在导航且路线上有途经点时，「换一个充电站」是改这趟路线——挑站与改路线都归导航
        # （原站附近找同类、原位替换、重算全程），这里零播报改派过去；没有活动路线时照旧往下找站。
        swap = asks_to_swap_charger(intent.raw_text or "")
        if swap and self._route_has_stops(meta):
            return AgentResult(speech="", data={"_escalate": {
                "intent": "navigation.reroute", "slots": {}, "reason": "swap_route_charger"}})
        # 没有活动路线的「换一个充电站」：排除这一会话里推荐过的站（此前照常找站，又推荐同一个——CA2-19 §9.1）
        seen = await self._recommended(ctx) if swap else []
        # 读电量（真实车辆电量优先，回退 memory）
        soc = await self._resolve_soc(ctx, meta)

        prefer = (intent.slots.get("prefer") or "").strip()
        charger_type = "快充" if "快" in prefer else ""

        # 「导航去X + 在附近找充电桩」：按目的地搜，最优站经聚合器并入导航路线作为途经点。
        # 泛目的地（含裸城市名）与 plan 同门：先 dest_choice 澄清（R1，B2-3/B5-2）。
        destination = (intent.slots.get("destination") or "").strip()
        if destination:
            destination = await self._resolve_dest_ordinal(ctx, destination)
            clarify = await self._clarify_vague_destination(destination, meta, ctx=ctx)
            if clarify:
                return clarify
            return await self._find_near_destination(destination, charger_type, soc, meta, ctx=ctx, seen=seen)

        # 获取位置
        current = current_location_from_meta(meta)
        if current:
            near = GeoPoint(lat=current.lat, lng=current.lng)
        else:
            loc_values = await ctx.fetch("vehicle.location")
            location = loc_values.get("vehicle.location", "")
            near = GeoPoint(address=location) if location else GeoPoint()

        # 搜充电站
        try:
            stations = await self.charging.find_nearby(
                near, charger_type=charger_type, meta=meta)
        except ProviderError as e:
            # §9.5 铁律③：运行期真实源失败不改供 mock 假充电站（假站可能被用户导航过去）；
            # R9 契约：诚实话术用 OK 返回（FAILED 会被聚合器吞成裸「抱歉，处理失败」）。
            logger.warning("charging find failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(
                speech="充电站信息暂时拿不到（服务不可用），请稍后再试。",
                follow_up="稍后再说一次就行")

        if not stations:
            return AgentResult(speech="附近暂未找到充电站，请稍后重试。")

        # 排序（空闲优先 + 距离近）
        stations.sort(key=lambda s: (-s.available, s.distance_km))
        if seen:
            stations = [s for s in stations if not self._was_recommended(s, seen)]
            if not stations:
                return AgentResult(speech="附近能找到的充电站刚才都推荐过了。要换个地方找，可以说「到XX附近找充电站」。")

        # 组织回复：实时空闲已知（mock）才报"X/Y空闲"，高德基础 POI 未知时报距离/评分，不编造
        top3 = stations[:3]
        await self._remember_recommended(ctx, top3, seen)

        def _desc(s):
            if s.total > 0:
                return f"{s.name}（{s.available}/{s.total}空闲，{s.distance_km}km）"
            extra = f"，评分{s.rating}" if s.rating else ""
            return f"{s.name}（{s.distance_km}km{extra}）"

        names = "、".join(_desc(s) for s in top3)
        speech = f"为您找到 {len(stations)} 个充电站，推荐：{names}。需要导航过去吗？"
        items = [
            {"id": s.id, "name": s.name, "available": s.available,
             "total": s.total, "price": s.price_per_kwh,
             "distance_km": s.distance_km, "operator": s.operator}
            for s in stations
        ]
        return AgentResult(
            speech=speech,
            # CA2-19 S1：站点数据带来源（`_prov`），电量带时效与来源
            ui_card=attach({"type": "charging_list", "items": items, **self._soc_card(soc)},
                           self.charging),
            data={"items": items},
            follow_up="说『导航去第一个』或告诉我你的偏好",
        )

    async def _find_near_destination(self, destination: str, charger_type: str,
                                     soc: Reading, meta, *, ctx=None, seen=()) -> AgentResult:
        """按目的地搜充电站，把最优站作为导航途经点（出 charging_route 卡 + data.waypoint）。

        聚合器据 data.waypoint 把该站并入导航步的 navigate 动作（payload.waypoints），
        让“导航去X + 附近充电”产出带途经充电点的单条路线，而非孤立的充电列表。
        """
        # 视觉地标目的地（“像笋的建筑”）先解析成地图可检索的正式名再搜——否则高德 geocode
        # 不到原描述、会失败/限流回退假数据。候选优先，原描述兜底（与导航步同一共享解析器）。
        targets = [destination]
        if is_landmark_description(destination):
            cands = await landmark_candidates(self.llm, destination, logger=logger)
            if cands:
                targets = cands + [destination]

        resolved, stations, errored = destination, [], False
        for target in targets:
            try:
                stations = await self.charging.find_nearby(
                    GeoPoint(address=target), charger_type=charger_type, meta=meta)
            except ProviderError as e:
                logger.warning("charging find near %s failed（诚实降级，无 mock 回退）: %s",
                               target, e)
                errored = True
                stations = []
            if stations:
                resolved = target
                break

        if not stations:
            # §9.5 铁律③：真实源失败不改供 mock 假站；区分「服务坏了」与「真没有」，都诚实。
            if errored:
                return AgentResult(
                    speech=f"充电站信息暂时拿不到（服务不可用），到达{destination}后我再帮您找。")
            return AgentResult(
                speech=f"{destination}附近暂未找到充电站，到达后我再帮您找。")

        stations.sort(key=lambda s: (-s.available, s.distance_km))
        if seen:
            stations = [s for s in stations if not self._was_recommended(s, seen)]
            if not stations:
                return AgentResult(
                    speech=f"{destination}附近能找到的充电站刚才都推荐过了，到达后我再帮您找。")
        top = stations[0]
        await self._remember_recommended(ctx, [top], seen)
        # CA2-19 S3：选站理由可追溯；高德基础 POI 没有实时空闲，按距离选、如实标「空闲状态未知」
        reason = ("空闲桩最多" if top.total > 0 else "离目的地最近（空闲状态未知）")

        # 途经点契约：聚合器据此把该站并入导航 navigate 动作（payload.waypoints）
        # 理由只进 data.choice_reason：途经点会被聚合器原样并进导航动作载荷，不往里加字段
        waypoint = {"name": top.name, "address": top.address,
                    "lat": top.lat, "lng": top.lng}
        extra = (f"，{top.available}/{top.total}空闲" if top.total > 0
                 else (f"，评分{top.rating}" if top.rating else ""))
        dist = f"{top.distance_km}km" if top.distance_km else "目的地附近"
        # CA2-19 S3：只说推荐。这一步自己不发导航——同轮有导航时聚合器才把途经点并进导航动作；
        # 单独问「X附近有充电站吗」时没有路线，此前却说「已为前往X的路线加入途经充电站」
        speech = (f"前往{resolved}，可以在目的地附近的{top.name}补电"
                  f"（{dist}{extra}）。")
        # 复用 charging_route 卡：出发地 → ⚡该站 → 目的地
        # stops 带坐标（2026-09-11）：Android 地图页据此标出补电站；HMI 只读 name/address
        card = attach({"type": "charging_route", "display_priority": 0,
                       "destination": resolved,
                       "stops": [{"name": top.name, "address": top.address,
                                  "lat": top.lat, "lng": top.lng}],
                       **self._soc_card(soc)}, self.charging)
        items = [
            {"id": s.id, "name": s.name, "available": s.available,
             "total": s.total, "price": s.price_per_kwh,
             "distance_km": s.distance_km, "operator": s.operator,
             "lat": s.lat, "lng": s.lng, "availability_known": s.total > 0}
            for s in stations
        ]
        return AgentResult(
            speech=speech, ui_card=card,
            data={"waypoint": waypoint, "items": items, "choice_reason": reason},
            # 手机端把追问提示整句做成 chip、点按原样发出：提示本身就得是能用的说法（`runtime.charger_swap` 认得出）
            follow_up="想换一个充电站，说『换一个充电站』就行")

    @staticmethod
    async def _recommended(ctx) -> list[dict]:
        """这一会话里推荐过的站（`CHARGING_RECOMMENDED`）；读不到就是空，照常找站。"""
        try:
            data = await ctx.load_shared_state(CHARGING_RECOMMENDED)
            d = json.loads(data) if isinstance(data, str) else (data or {})
        except Exception:
            return []
        return [it for it in (d.get("items") or []) if isinstance(it, dict)] if isinstance(d, dict) else []

    @staticmethod
    async def _remember_recommended(ctx, picked: list, seen) -> None:
        """记下这一轮推荐的站：新的找站覆盖，换站时接在推荐过的后面（连着「换一个」一直往后推）。写不进去只影响下一次换站。"""
        if ctx is None:
            return
        items = list(seen) + [{"id": s.id, "name": s.name} for s in picked]
        try:
            await ctx.save_shared_state(CHARGING_RECOMMENDED, {"items": items[-20:]})
        except Exception as exc:
            logger.debug("recommended stations save skipped: %s", exc)

    @staticmethod
    def _was_recommended(station, seen) -> bool:
        return any((it.get("id") and it.get("id") == station.id) or it.get("name") == station.name for it in seen)

    # 行政区划级后缀——以此结尾的目的地视为"过泛"，先确认具体地点再规划途经点
    _ADMIN_SUFFIX = ("市", "省", "区", "县", "自治区", "自治州", "地区")

    @classmethod
    def _is_vague_destination(cls, dest: str) -> bool:
        """目的地是否过泛（行政区划级、无具体 POI 后缀）。"""
        d = (dest or "").strip()
        return bool(d) and d.endswith(cls._ADMIN_SUFFIX)

    async def _resolve_dest_ordinal(self, ctx, dest: str) -> str:
        """dest_choice 续接：destination=「第一个」这类字面序号 → 按上一轮候选回填真名（判据见 `agents._sdk.dest_choice`）。

        引擎补槽把用户原话原样灌进槽位（旅程 B2-3：真栈拿「第一个」去搜 POI，选到当前
        位置旁的无关站）。候选由 `_clarify_vague_destination` 写入 CHARGING_DEST_CHOICES。"""
        return await resolve_ordinal(ctx, CHARGING_DEST_CHOICES, dest)

    async def _clarify_vague_destination(self, dest: str, meta,
                                         ctx=None) -> AgentResult | None:
        """目的地过泛 → dest_choice 澄清卡；不过泛返回 None（plan 与 find 共用）。

        判定两层：①行政区划后缀（"兰州市"）；②R1（旅程 B2-3/B5-2）：裸城市名
        （「惠州」不带 市 后缀）绕过后缀判定 → 就近关键词搜出 0.3km 的「惠州出口」
        当目的地——短名补 geocode level 权威判定，探测失败 fail-open 不拦。
        这是澄清式 NEED_SLOT（编排器用用户回复回填 destination 重跑本步）。
        """
        vague = self._is_vague_destination(dest)
        if not vague and 2 <= len(dest) <= 4:
            level_fn = getattr(getattr(self.charging, "_poi", None), "geocode_level", None)
            if level_fn:
                try:
                    level, _loc = await level_fn(dest, meta=meta)
                    vague = level in ("国家", "省", "市", "区县")
                except Exception as e:
                    logger.debug("charging dest level probe failed: %s", e)
        if not vague:
            return None
        candidates = []
        try:
            raw = await self.charging.suggest_destinations(dest, meta=meta)
            # 丢弃仍是行政区划级的候选（如"兰州市"自身），否则选它会再次触发追问
            candidates = [c for c in raw
                          if c.get("name") and not self._is_vague_destination(c["name"])]
        except ProviderError as e:
            logger.warning("charging suggest destinations failed: %s", e)
        if candidates:
            names = "、".join(c["name"] for c in candidates[:3])
            # 候选落共享态（序=卡片渲染序）：续接轮「第N个」由 _resolve_dest_ordinal 回填
            await save_choices(ctx, CHARGING_DEST_CHOICES, candidates)
            return AgentResult(
                status=NEED_SLOT, missing_slots=["destination"],
                speech=f"{dest}范围比较大，您具体要去哪个？例如{names}。"
                       f"说出名称或『第几个』，也可以直接告诉我详细地址。",
                # purpose=dest_choice 让 HMI 把"第N个"回填为目的地槽位（而非发起导航）
                ui_card=attach({"type": "poi_list", "purpose": "dest_choice",
                                "display_priority": 1,
                                "title": f"{dest} · 选择目的地",
                                "items": [{"id": c.get("id", ""), "name": c["name"],
                                           "address": c.get("address", "")}
                                          for c in candidates]}, self.charging),
                follow_up="选择具体目的地")
        return AgentResult(
            status=NEED_SLOT, missing_slots=["destination"],
            speech=f"{dest}范围比较大，您具体要去哪里？比如火车站、机场，"
                   f"或告诉我详细地址，我再为您规划沿途充电。",
            follow_up="告诉我具体地点")

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        """规划长途充能策略。"""
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您要去哪里？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        # 目的地过泛（如"兰州市"/裸「惠州」）→ 先二次确认具体地点，再据此规划途经点。
        dest = await self._resolve_dest_ordinal(ctx, dest)
        clarify = await self._clarify_vague_destination(dest, meta, ctx=ctx)
        if clarify:
            return clarify

        soc = await self._resolve_soc(ctx, meta)

        # 调充电 Provider 规划（高德：真实路线距离/时长 + 目的地附近真实充电站）。
        # 电量读不到就传 None：provider 不判断够不够、不拿缺省值代替（CA2-19 S1）
        try:
            plan = await self.charging.plan_route(dest, soc=soc.percent(), meta=meta,
                                                  soc_note=soc.spoken_note())
        except ProviderError as e:
            # §9.5 铁律③：不出 mock 假路线卡；R9 契约：OK 话术（FAILED 会被聚合器吞成裸报错）。
            logger.warning("charging plan failed（诚实降级，无 mock 回退）: %s", e)
            return AgentResult(speech="充电规划服务暂时不可用，请稍后再试。")

        # 信息建议（advisory）：充能路线卡 = 出发地→沿途途经充电点→目的地，不二次确认、
        # 不发导航动作（导航由「导航」步处理）。专属 type 让聚合器在多意图下优先展示它
        # （否则只取首个卡=导航候选，充电途经点不可见）。
        # 几何（2026-09-11）：stops 坐标 / 起点 / 折线只在 provider 给了时写，Android 地图页据此画「查看路线」
        card = attach({
            "type": "charging_route",
            "display_priority": 0,
            "destination": dest,
            "distance_km": plan.distance_km,
            "duration_min": plan.total_duration_min,
            "stops": [{"name": s.get("name", ""), "address": s.get("address", ""),
                       "at_km": s.get("at_km"),
                       # CA2-19 S3：到站估算电量与选择理由（只有读到电量才有）
                       **({"arrive_soc": s["arrive_soc"], "reason": s.get("reason", "")}
                          if s.get("arrive_soc") is not None else {}),
                       **({"lat": s["lat"], "lng": s["lng"]}
                          if s.get("lat") is not None and s.get("lng") is not None else {})}
                      for s in plan.stops],
            **({"arrive_soc": plan.arrive_soc} if plan.arrive_soc is not None else {}),
            **self._soc_card(soc),
            **({"origin_loc": plan.origin_loc} if plan.origin_loc else {}),
            **({"path": plan.path} if plan.path else {}),
        } if plan.distance_km > 0 else None,  # 无路线（需定位/取路失败）→ 纯语音
            self.charging)
        return AgentResult(
            speech=plan.summary.rstrip("。") + "。",   # provider summary 可能已带句号，避免"。。"
            ui_card=card,
            data={"stops": plan.stops, "summary": plan.summary,
                  **({"arrive_soc": plan.arrive_soc} if plan.arrive_soc is not None else {})},
        )

    async def _status(self, intent, ctx, meta) -> AgentResult:
        """查询当前充电状态。"""
        reading = await self._resolve_soc(ctx, meta)
        pct = reading.percent()
        if pct is None:
            # CA2-19 S1：读不到就如实说，不报缺省值
            return AgentResult(speech="暂时没读到当前电量，可以看一下仪表盘。",
                               data={"battery": "", **reading.card_fields("battery")})
        return AgentResult(
            speech=f"当前电量：{pct}%{reading.spoken_note()}。",
            data={"battery": f"{pct}%", **reading.card_fields("battery")},
        )
