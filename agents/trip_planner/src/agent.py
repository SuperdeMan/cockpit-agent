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
from agents.navigation.src.providers import build_poi_provider
from agents.navigation.src.providers.mock import MockPOIProvider
from .models import Trip
from .pipeline import build_poi_pool, propose, ground, solve, narrate

logger = logging.getLogger("agent.trip_planner")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
_PROFILE_KEY = "trip_active"

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_MOD_DAY_RE = re.compile(r"第\s*([一二两三四五六七八九十0-9]+)\s*天")


class TripPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        # 进程内复用 navigation 的 POI provider（接地景点/充电站 + 算 leg 路线），
        # 跟随 charging_planner 先例，避免每 leg 跨 gRPC。真实 provider 抖动降级 mock。
        self.poi = build_poi_provider()
        self._fallback = MockPOIProvider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {"trip.plan": self._plan, "trip.modify": self._modify}
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
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您想去哪里玩？",
                follow_up="请告诉我目的地", missing_slots=["destination"])
        days = (intent.slots.get("days") or "").strip()
        prefs = (intent.slots.get("preferences") or "").strip()

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
        n = self._modify_day(modification)
        if n and trip.day(n):
            # 只重规划第 n 天：其余 Day 对象原样保留（结构化天然不漂移）。
            pool = await build_poi_pool(self.poi, self._fallback, dest, prefs, None, meta)
            sk = await propose(self.llm, dest, "1", prefs,
                               [p.name for p in pool], modification)
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
            soc = await self._soc_pct(ctx, meta)
            trip = await solve(self.poi, self._fallback, trip, soc, meta)
        else:
            # 定位不到具体天 → 整程重规划（把修改并入偏好上下文）。
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

    @staticmethod
    def _modify_day(text: str) -> int:
        """从修改话术解析「第N天」的天号；解析不到返回 0。"""
        m = _MOD_DAY_RE.search(text or "")
        if not m:
            return 0
        tok = m.group(1)
        return int(tok) if tok.isdigit() else _CN_NUM.get(tok, 0)
