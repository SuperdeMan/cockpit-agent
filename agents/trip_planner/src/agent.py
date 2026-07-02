"""行程规划 Agent（P0 重构）—— 结构化可执行行程 + 充电感知 + 落 memory。

把项目铁律「规划/执行分离、LLM 提议、确定性 Executor 落地」下沉到 trip-planner 内部：
`_plan` 不再让 LLM 自由文本直出整份行程，而是驱动 `pipeline` 四段
（propose 提议骨架 → ground 接地真实 POI → solve 算车程/编织充电 → narrate 出话术+卡），
产出结构化 `Trip`（`models.Trip`）。状态落 memory（profile KV `trip_active`），Agent 无状态化。

provider 在进程内复用 navigation 的 `POIProvider`（跟随 charging_planner 先例）。
确认轮（`meta.confirmed=="true"`）→ `_finalize` 直接收尾、绝不再 NEED_CONFIRM（防死循环）。
"""
from __future__ import annotations
import json
import logging
import os
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM
from agents._sdk.location import current_location_from_meta
from agents.navigation.src.providers import build_poi_provider
from agents.navigation.src.providers.mock import MockPOIProvider
from .models import Trip, Stop
from .pipeline import (build_poi_pool, propose, ground, solve, narrate,
                       _ground_one, _poi_to_dict)
from .extract import extract_trip

logger = logging.getLogger("agent.trip_planner")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
_PROFILE_KEY = "trip_active"

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_MOD_DAY_RE = re.compile(r"第\s*([一二两三四五六七八九十0-9]+)\s*天")
_ORDINAL_RE = re.compile(r"第\s*([一二两三四五六七八九十0-9]+)\s*[个站]")
# 换/调整某站时若指定了换成什么（『第二站换成西湖』），取目标名
_REPLACE_TARGET_RE = re.compile(r"(?:换成|改成|换为|改为|换到)\s*([^，。,、\s]{2,12})")
_DAY_PREFIX_RE = re.compile(r"^第\s*[一二两三四五六七八九十0-9]+\s*天的?")
# 结构化编辑：删/加某个具体停靠点（『换』走整天重规划，不在此匹配）
_REMOVE_RE = re.compile(r"(?:删掉|删除|去掉|不去|不想去|不要去|去不了|取消)\s*([^，。,、\s了]{2,12})")
_ADD_RE = re.compile(r"(?:加一个|加个|再加|增加|多加|加上|想去|顺便去|顺路去)\s*([^，。,、\s]{2,12})")


class TripPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 进程内复用 navigation 的 POI provider（接地景点/充电站 + 算 leg 路线），
        # 跟随 charging_planner 先例，避免每 leg 跨 gRPC。真实 provider 抖动降级 mock。
        self.poi = build_poi_provider()
        self._fallback = MockPOIProvider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {"trip.plan": self._plan, "trip.modify": self._modify,
                    "trip.navigate": self._navigate, "trip.status": self._status,
                    "trip.reschedule": self._reschedule}
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status="failed", speech="行程助手暂不支持该请求。")

    # ── 电量 ───────────────────────────────────────────────────
    async def _soc_pct(self, ctx, meta) -> float:
        """当前电量百分比：优先边端注入的真实车辆电量，回退 memory，再回退 50%。
        与 charging_planner._resolve_soc 同源，保证多日行程起点 SoC 与仪表一致。"""
        soc = str((meta or {}).get("vehicle_battery", "") or "").strip()
        if not soc:
            try:
                vals = await ctx.fetch("vehicle.battery")
                soc = vals.get("vehicle.battery", "")
            except Exception:
                soc = ""
        try:
            return float(str(soc).replace("%", "").strip()) or 50.0
        except ValueError:
            return 50.0

    # ── 持久化（memory profile KV；Agent 无状态化）───────────────
    async def _load_trip(self, ctx) -> Trip | None:
        """从 memory 读当前活动行程。失败/无 → None。"""
        try:
            vals = await ctx.fetch(f"profile.{_PROFILE_KEY}")
        except Exception as e:
            logger.warning("load trip failed: %s", e)
            return None
        raw = vals.get(f"profile.{_PROFILE_KEY}")
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return None
        return Trip.from_dict(raw) if isinstance(raw, dict) else None

    async def _save_trip(self, ctx, trip: Trip) -> None:
        """写当前活动行程到 memory（best-effort，失败不阻断规划）。"""
        try:
            await ctx.save_profile(_PROFILE_KEY, trip.to_dict())
        except Exception as e:
            logger.warning("save trip failed: %s", e)

    # ── 规划流水线 ─────────────────────────────────────────────
    async def _run_pipeline(self, ctx, meta, dest: str, days: str, prefs: str,
                            raw_text: str) -> Trip:
        """propose → ground → solve，产出结构化 Trip。"""
        # 目的地是行程城市（非当前位置）→ pool 搜索 near=None，靠关键词「{dest} 景点」定位。
        pool = await build_poi_pool(self.poi, self._fallback, dest, prefs, None, meta)
        skeleton = await propose(self.llm, dest, days, prefs,
                                 [p.name for p in pool], raw_text)
        trip = await ground(self.poi, self._fallback, skeleton, pool, meta,
                            dest=dest, days=days, prefs=prefs, raw_text=raw_text,
                            llm=self.llm)
        soc = await self._soc_pct(ctx, meta)
        trip = await solve(self.poi, self._fallback, trip, soc, meta)
        trip.session_id = ctx.session_id or ""
        trip.user_id = ctx.user_id or ""
        return trip

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, meta)

        dest = (intent.slots.get("destination") or "").strip()
        days = (intent.slots.get("days") or "").strip()
        prefs = (intent.slots.get("preferences") or "").strip()
        if not dest:
            # 确定性路由（manifest route_hints）注入的 trip.plan 步 slots 为空——
            # 从原话抽取目的地/天数/偏好（原编排核心 _extract_trip 的领域逻辑，R2.1 搬回本 Agent）。
            edest, edays, eprefs = extract_trip(intent.raw_text or "")
            dest, days, prefs = dest or edest, days or edays, prefs or eprefs
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您想去哪里玩？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        trip = await self._run_pipeline(ctx, meta, dest, days, prefs, intent.raw_text)
        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{speech}\n\n确认按此方案出行吗？",
            ui_card=card,
            follow_up="说『确认』即可，或告诉我需要调整的地方",
        ).action("trip.plan", {"destination": dest, "days": str(trip.days)},
                 require_confirm=True)

    async def _modify(self, intent, ctx, meta) -> AgentResult:
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, meta)

        modification = (intent.slots.get("modification") or "").strip() \
            or (intent.raw_text or "").strip()
        if not modification:
            return AgentResult(
                status=NEED_SLOT, speech="您想怎么调整行程？",
                follow_up="例如：第二天换成宋城", missing_slots=["modification"])

        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(
                status=NEED_SLOT,
                speech="还没有正在规划的行程，您想去哪里玩几天？",
                follow_up="例如：周末去杭州两天", missing_slots=["destination"])

        dest = trip.destination
        prefs = "、".join(trip.preferences)
        soc = await self._soc_pct(ctx, meta)

        # ① 结构化编辑优先：加/删某个具体停靠点（只动受影响项，跨天去重）。
        if await self._apply_structural_edit(trip, modification, meta):
            trip = await solve(self.poi, self._fallback, trip, soc, meta)
        else:
            n = self._modify_day(modification)
            if n and trip.day(n):
                # ② 只重规划第 n 天：其余 Day 原样保留（结构化天然不漂移）。
                pool = await build_poi_pool(self.poi, self._fallback, dest, prefs, None, meta)
                # 跨天去重：重规划某天时排除其它天已用景点，避免改完与别天撞车。
                used = {s.name for d in trip.itinerary if d.day_index != n for s in d.stops}
                names = [p.name for p in pool if p.name not in used]
                sk = await propose(self.llm, dest, "1", prefs, names, modification)
                oneday = await ground(self.poi, self._fallback, sk, pool, meta,
                                      dest=dest, prefs=prefs, raw_text=modification,
                                      llm=self.llm)
                if oneday.itinerary and oneday.itinerary[0].stops:
                    newday = oneday.itinerary[0]
                    newday.day_index = n
                    for idx, d in enumerate(trip.itinerary):
                        if d.day_index == n:
                            trip.itinerary[idx] = newday
                            break
                trip = await solve(self.poi, self._fallback, trip, soc, meta)
            else:
                # ③ 定位不到具体天 → 整程重规划（把修改并入偏好上下文）。
                trip = await self._run_pipeline(
                    ctx, meta, dest, str(trip.days or ""),
                    f"{prefs} {modification}".strip(), modification)

        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{speech}\n\n确认按此调整吗？",
            ui_card=card,
            follow_up="说『确认』即可",
        ).action("trip.modify", {"modification": modification}, require_confirm=True)

    async def _apply_structural_edit(self, trip: Trip, modification: str, meta) -> bool:
        """结构化编辑：删/加某个具体停靠点。命中并改动返回 True；否则 False（交给重规划）。"""
        day_n = self._modify_day(modification)
        m = _REMOVE_RE.search(modification)
        if m:
            name = _DAY_PREFIX_RE.sub("", m.group(1)).strip("的了 ")
            if name and self._remove_stop(trip, name, day_n):
                return True
        m = _ADD_RE.search(modification)
        if m:
            name = _DAY_PREFIX_RE.sub("", m.group(1)).strip("的了 ")
            if name and await self._add_stop(trip, name, day_n, meta):
                return True
        # 换/调整第N天第M站 → 替换那个具体停靠点（根治"调整某站却返回原样"的 no-op）
        if await self._replace_stop(trip, modification, meta):
            return True
        return False

    @staticmethod
    def _remove_stop(trip: Trip, name: str, day_n: int = 0) -> bool:
        name = (name or "").strip()
        for dy in trip.itinerary:
            if day_n and dy.day_index != day_n:
                continue
            for k, s in enumerate(dy.stops):
                nm = s.name or ""
                if nm and (name in nm or nm in name):
                    dy.stops.pop(k)
                    return True
        return False

    async def _add_stop(self, trip: Trip, name: str, day_n: int, meta) -> bool:
        # 跨天去重：已在行程里就视为已满足，不重复加、也不触发重规划。
        for dy in trip.itinerary:
            for s in dy.stops:
                nm = s.name or ""
                if nm and (name in nm or nm in name):
                    return True
        poi = await _ground_one(self.poi, self._fallback, name, None, meta, self.llm)
        if not (poi and poi.lat and poi.lng):
            return False
        nstops = sum(len(d.stops) for d in trip.itinerary)
        stop = Stop(stop_id=f"s_add{nstops + 1}", name=poi.name or name,
                    type="attraction", dwell_min=120, source="user",
                    poi=_poi_to_dict(poi), grounded=True)
        target = trip.day(day_n) if day_n else min(
            trip.itinerary, key=lambda d: len(d.stops))
        (target or trip.itinerary[0]).stops.append(stop)
        return True

    async def _replace_stop(self, trip: Trip, modification: str, meta) -> bool:
        """换/调整「第N天第M站」：替换那个具体停靠点。指定『换成X』用 X，否则从池里挑一个
        行程没用过的不同景点（根治『调整第N站』整天重规划又挑回原样的 no-op）。"""
        if not any(k in modification for k in ("调整", "换", "改", "替换")):
            return False
        n = self._modify_day(modification)
        m = self._parse_ordinal(modification)
        day = trip.day(n) if n else None
        if not (n and m and day and m <= len(day.stops)):
            return False
        used = {s.name for d in trip.itinerary for s in d.stops}
        tm = _REPLACE_TARGET_RE.search(modification)
        if tm:                                   # 指定换成 X → 接地 X
            poi = await _ground_one(self.poi, self._fallback,
                                    tm.group(1).strip(), None, meta, self.llm)
        else:                                    # 没指定 → 池里挑一个没用过的不同景点
            pool = await build_poi_pool(self.poi, self._fallback, trip.destination,
                                        "、".join(trip.preferences), None, meta)
            poi = next((p for p in pool if p.name not in used and p.lat and p.lng), None)
        if not (poi and poi.lat and poi.lng):
            return False
        old = day.stops[m - 1]
        day.stops[m - 1] = Stop(stop_id=old.stop_id, name=poi.name, type=old.type,
                                dwell_min=old.dwell_min, source="user",
                                poi=_poi_to_dict(poi), grounded=True)
        return True

    async def _finalize(self, ctx, meta) -> AgentResult:
        """确认收尾：把行程第一个已接地停靠点作导航第一站，给候选 POI 让用户选『第几个』。
        绝不再 NEED_CONFIRM。状态置 confirmed 并持久化。"""
        trip = await self._load_trip(ctx)
        if not trip:
            return AgentResult(speech="好的，行程已确认，祝您旅途愉快！")

        dest = trip.destination
        day_txt = f"{trip.days}天" if trip.days else ""
        first = trip.first_stop()
        items, label = [], dest

        if first:
            label = first.name
            try:    # 实时搜第一站候选（如「天坛公园」多个门）供「第N个」就近导航
                results = await self.poi.search(first.name, limit=5, meta=meta)
                items = [{"id": r.id, "name": r.name, "address": r.address,
                          "rating": r.rating, "lat": r.lat, "lng": r.lng}
                         for r in results if r.name]
            except Exception as e:
                logger.warning("finalize first-stop search failed: %s", e)
            if not items and first.poi:     # 搜不到退化到接地时的 POI
                items = [first.poi]

        trip.status = "confirmed"
        await self._save_trip(ctx, trip)

        if items:
            names = "、".join(i["name"] for i in items[:3])
            return AgentResult(
                speech=f"好的，{dest}{day_txt}的行程已确认！第一站为您安排在「{label}」："
                       f"{names}。说『第几个』我就为您导航过去。",
                ui_card={"type": "poi_list", "title": f"{label} · 选择第一站",
                         "items": items},
                follow_up="说『第一个』即可开始导航")
        return AgentResult(
            speech=f"好的，{dest}{day_txt}的行程已确认，祝您和家人旅途愉快！"
                   f"出发时说『导航去{label}』我就为您开始导航。")

    # ── 在途导航：把行程里任意停靠点变成一句话可导航（P1）──────────
    async def _navigate(self, intent, ctx, meta) -> AgentResult:
        """导航到当前行程里的某个停靠点：『下一站』/『第N天的X』/『第N天第M个』/『行程里的X』。

        从持久化 Trip 取已接地停靠点，按指代定位后发 navigate 动作，并推进 cursor。
        无行程 → 引导先规划（普通导航仍由 navigation 处理，本意图只在确定性路由命中行程指代时触发）。
        """
        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(
                status=NEED_SLOT,
                speech="还没有规划好的行程，先告诉我去哪里玩几天，我规划好就能带您一站站去。",
                follow_up="例如：周末去杭州两天", missing_slots=["destination"])

        flat = self._flatten_grounded(trip)
        if not flat:
            return AgentResult(speech="行程里还没有可导航的具体地点。")

        raw = intent.raw_text or ""
        target_slot = (intent.slots.get("target") or "").strip()
        day_n = self._modify_day(intent.slots.get("day") or raw)
        ordinal = self._parse_ordinal(intent.slots.get("stop") or raw)
        is_next = (target_slot == "next" or "下一站" in raw or "下个" in raw
                   or "继续导航" in raw)

        picked = None
        if is_next:
            picked = self._next_after_cursor(trip, flat)
            if picked is None:
                return AgentResult(speech="行程已经到最后一站啦，没有下一站了。")
        else:
            name = target_slot or self._strip_nav_prefix(raw)
            if name:
                picked = self._find_by_name(flat, name, day_n)
            if picked is None and day_n:
                picked = self._find_by_day_ordinal(flat, day_n, ordinal or 1)

        if picked is None:
            return AgentResult(
                speech="没找到您说的那一站，可以说『下一站』，或『第二天的西湖』。",
                follow_up="说『下一站』或『第N天的某地点』")

        dy, gi, stop = picked
        trip.cursor = {"day_index": dy, "stop_index": gi}
        await self._save_trip(ctx, trip)
        poi = stop.poi or {}
        payload = {"destination": stop.name, "lat": poi.get("lat"), "lng": poi.get("lng")}
        cur = current_location_from_meta(meta)
        if cur:
            payload.update(origin_lat=cur.lat, origin_lng=cur.lng)
        return AgentResult(
            speech=f"好的，为您导航到第{dy}天的{stop.name}。",
            data={"destination": stop.name, "lat": poi.get("lat"), "lng": poi.get("lng")},
        ).action("navigate", payload)

    # ── 在途状态查询（P2，只读）─────────────────────────────────
    async def _status(self, intent, ctx, meta) -> AgentResult:
        """在途进度：在第几站/下一站/还剩几站/全程补电几次。不改行程。"""
        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(speech="您还没有规划行程。说『去某地玩几天』我就帮您安排。")
        flat = self._flatten_grounded(trip)
        total = len(flat)
        cur = trip.cursor or {}
        cd, ci = cur.get("day_index", 0), cur.get("stop_index", 0)
        pos = next((k for k, (d, i, _s) in enumerate(flat) if d == cd and i == ci), -1)
        remaining = flat[pos + 1:] if pos >= 0 else flat
        charge_total = sum(len(leg.charging_stops)
                           for dy in trip.itinerary for leg in dy.legs)
        parts = [f"您正在{trip.destination}{trip.days}天行程（共{total}站）"]
        if pos >= 0:
            parts.append(f"已到第{pos + 1}站「{flat[pos][2].name}」")
        if remaining:
            parts.append(f"下一站是「{remaining[0][2].name}」，后面还有{len(remaining)}站")
        else:
            parts.append("行程已全部走完")
        if charge_total:
            parts.append(f"全程需补电{charge_total}次")
        return AgentResult(
            speech="，".join(parts) + "。", ui_card=trip.card_dict(),
            data={"total": total, "remaining": len(remaining), "charging": charge_total})

    # ── 在途重排：确定性精简剩余行程（P2）──────────────────────
    async def _reschedule(self, intent, ctx, meta) -> AgentResult:
        """时间不够/太累/想提前回 → 确定性砍尾部停靠点或最后一天，二次确认。"""
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, meta)
        trip = await self._load_trip(ctx)
        if not trip or not trip.itinerary:
            return AgentResult(
                status=NEED_SLOT, speech="还没有规划好的行程，您想去哪里玩几天？",
                follow_up="例如：周末去杭州两天", missing_slots=["destination"])
        hint = (intent.slots.get("hint") or intent.raw_text or "")
        if not self._trim_itinerary(trip, hint):
            return AgentResult(
                speech="行程已经很精简了，没有可再删减的安排啦。",
                ui_card=trip.card_dict())
        soc = await self._soc_pct(ctx, meta)
        trip = await solve(self.poi, self._fallback, trip, soc, meta)
        await self._save_trip(ctx, trip)
        speech, card = narrate(trip)
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"已为您精简行程：{speech}\n\n确认按此调整吗？",
            ui_card=card, follow_up="说『确认』即可",
        ).action("trip.reschedule", {"hint": hint}, require_confirm=True)

    @staticmethod
    def _trim_itinerary(trip: Trip, hint: str) -> bool:
        """确定性精简：想提前回→删最后一天；时间不够/太累→每个剩余天删尾部一站。返回是否改动。"""
        h = hint or ""
        if (any(k in h for k in ("提前回", "早点回", "早些回", "少一天", "回家"))
                and len(trip.itinerary) > 1):
            trip.itinerary.pop()
            return True
        cd = (trip.cursor or {}).get("day_index", 0)
        changed = False
        for dy in trip.itinerary:
            if dy.day_index < cd:               # 已过的天不动
                continue
            if len(dy.stops) > 1:
                dy.stops.pop()
                changed = True
        return changed

    @staticmethod
    def _flatten_grounded(trip: Trip) -> list:
        """按天序展开所有已接地停靠点：[(day_index, grounded_idx_in_day, Stop)]。"""
        out = []
        for dy in trip.itinerary:
            gi = 0
            for s in dy.stops:
                if getattr(s, "grounded", False) and (s.poi or {}).get("lat"):
                    out.append((dy.day_index, gi, s))
                    gi += 1
        return out

    @staticmethod
    def _next_after_cursor(trip: Trip, flat: list):
        """cursor 之后的下一站；cursor 未命中（初始 0,0）→ 首站；已是末站 → None。"""
        cur = trip.cursor or {}
        cd, ci = cur.get("day_index", 0), cur.get("stop_index", 0)
        for k, (d, i, _s) in enumerate(flat):
            if d == cd and i == ci:
                return flat[k + 1] if k + 1 < len(flat) else None
        return flat[0] if flat else None

    @staticmethod
    def _find_by_day_ordinal(flat: list, day_n: int, m: int):
        inday = [t for t in flat if t[0] == day_n]
        if not inday:
            return None
        idx = max(1, m) - 1
        return inday[idx] if idx < len(inday) else inday[-1]

    @staticmethod
    def _find_by_name(flat: list, name: str, day_n: int = 0):
        name = (name or "").strip()
        if not name:
            return None
        scoped = [t for t in flat if (not day_n or t[0] == day_n)]
        for pool in (scoped, flat):           # 先按指定天找，再跨天兜底
            for t in pool:
                nm = t[2].name or ""
                if name in nm or nm in name:
                    return t
        return None

    @staticmethod
    def _parse_ordinal(text: str) -> int:
        m = _ORDINAL_RE.search(text or "")
        if not m:
            return 0
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)

    @staticmethod
    def _strip_nav_prefix(raw: str) -> str:
        """从『导航去第二天的西湖』剥成『西湖』；非具体地点指代返回空。"""
        t = (raw or "").strip()
        for p in ("导航去", "导航到", "导航", "带我去", "去", "到"):
            if t.startswith(p):
                t = t[len(p):]
                break
        t = re.sub(r"^第\s*[一二两三四五六七八九十0-9]+\s*天的?", "", t)
        t = re.sub(r"^第\s*[一二两三四五六七八九十0-9]+\s*个", "", t)
        t = re.sub(r"^行程(里|中)?的?", "", t).strip("的里中 ，。")
        return "" if t in ("下一站", "下个", "行程", "") else t

    @staticmethod
    def _modify_day(text: str) -> int:
        """从修改话术解析「第N天」的天号；解析不到返回 0。"""
        m = _MOD_DAY_RE.search(text or "")
        if not m:
            return 0
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)
