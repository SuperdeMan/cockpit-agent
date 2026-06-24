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
import re

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, NEED_CONFIRM

# 从行程文本里提取「第一天」的主要景点，作为确认后的导航第一站。
_DAY1_POI_RE = re.compile(
    r"第\s*[一1]\s*天[^第]{0,100}?"
    r"([一-鿿]{2,10}?(?:公园|长城|故宫|寺|塔|宫|山|湖|园|广场|博物馆|大街|"
    r"古镇|海洋馆|动物园|步行街|景区|村))")
# 去掉景点名前误粘的时间/动词（"下午可前往天坛公园" → "天坛公园"）
_DAY1_LEAD_RE = re.compile(
    r"^(上午|下午|傍晚|晚上|中午|早上|清晨|可|先|再|然后|接着|建议|前往|游览|参观|游玩|"
    r"散步|漫步|逛|去|到达|抵达|乘车|入住|后|的)+")


def _first_stop_from_itinerary(itinerary: str) -> str:
    """解析行程第一天的主要景点名（确认后据此搜 POI、设为导航第一站）。解析不到返回空。"""
    m = _DAY1_POI_RE.search(itinerary or "")
    if not m:
        return ""
    name = _DAY1_LEAD_RE.sub("", m.group(1))
    return name if len(name) >= 2 else m.group(1)

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
        # PoC：会话级行程缓存（session_id -> {destination,days,itinerary,pois}）。
        # 支撑「改第N天」局部重规划（需原行程上下文）与确认后取第一站候选 POI。
        # 单实例内存态，重启即失；量产应落到 memory 服务而非 Agent 本地。
        self._sessions: dict[str, dict] = {}

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "trip.plan": self._plan,
            "trip.modify": self._modify,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status="failed", speech="行程助手暂不支持该请求。")

    def _remember(self, sid: str, *, destination: str, days: str, itinerary: str,
                  pois: list | None = None, first_stop: str | None = None) -> None:
        """缓存本会话最近一次行程上下文。pois/first_stop 缺省时沿用旧值。"""
        if not sid:
            return
        if len(self._sessions) > 200:        # 轻量上限，避免无界增长
            self._sessions.clear()
        prev = self._sessions.get(sid, {})
        self._sessions[sid] = {
            "destination": destination or prev.get("destination", ""),
            "days": days or prev.get("days", ""),
            "itinerary": itinerary,
            "pois": pois if pois is not None else prev.get("pois", []),
            "first_stop": first_stop if first_stop is not None else prev.get("first_stop", ""),
        }

    async def _finalize(self, ctx, sid: str, dest: str, days: str) -> AgentResult:
        """确认后收尾：把行程「第一天」的景点设为导航第一站，给候选 POI 让用户选『第几个』。

        优先按第一天景点名（如天坛公园）实时搜 POI——这才是用户要去的第一站；
        搜不到再退化到规划时缓存的热门景点；再不行直接确认+导航目的地。
        plain poi_list（无 purpose）→ HMI 把『第N个』改写成就近导航。绝不 NEED_CONFIRM。"""
        st = self._sessions.get(sid or "", {})
        dest = dest or st.get("destination", "")
        days = days or st.get("days", "")
        day_txt = f"{days}天" if days else ""
        first_stop = st.get("first_stop", "")

        # 1) 第一天景点 → 实时搜 POI（确认后才搜，避免规划期多一次往返）
        items, label = [], first_stop or dest
        if first_stop:
            try:
                r = await self.agents.call("navigation", "navigation.search_poi",
                                           {"keyword": first_stop}, ctx)
                if isinstance(r, AgentResult) and r.ui_card:
                    items = (r.ui_card.get("items") or [])[:5]
            except Exception:
                items = []
        # 2) 退化到规划期缓存的热门景点
        if not items:
            items, label = (st.get("pois") or [])[:5], dest

        items = [{"id": p.get("id", ""), "name": p.get("name", ""),
                  "address": p.get("address", ""), "rating": p.get("rating"),
                  "lat": p.get("lat"), "lng": p.get("lng")}
                 for p in items if p.get("name")]
        if items:
            names = "、".join(i["name"] for i in items[:3])
            lead = (f"第一站为您安排在「{first_stop}」" if first_stop
                    else "第一站可以从这些热门景点开始")
            return AgentResult(
                speech=f"好的，{dest}{day_txt}的行程已确认！{lead}："
                       f"{names}。说『第几个』我就为您导航过去。",
                ui_card={"type": "poi_list", "title": f"{label} · 选择第一站",
                         "items": items},
                follow_up="说『第一个』即可开始导航")
        return AgentResult(
            speech=f"好的，{dest}{day_txt}的行程已确认，祝您和家人旅途愉快！"
                   f"出发时说『导航去{first_stop or dest}』我就为您开始导航。")

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        """规划行程。"""
        dest = intent.slots.get("destination", "")
        if not dest:
            return AgentResult(
                status=NEED_SLOT, speech="您想去哪里玩？",
                follow_up="请告诉我目的地", missing_slots=["destination"])

        days = intent.slots.get("days", "")
        prefs = intent.slots.get("preferences", "")

        # 用户已二次确认（编排器只对挂起那一步注入 confirmed）→ 收尾，不再重规划。
        # 否则确认轮会重跑 _plan 又返回 NEED_CONFIRM，陷入"确认→再规划→再确认"死循环。
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, ctx.session_id, dest, days)

        # 跨 Agent 协作：并行调用导航 + 天气 + 充电
        pois_info = ""
        weather_info = ""
        charging_info = ""
        first_pois: list = []          # Day1 候选景点（确认后作第一站供用户选）
        try:
            results = await asyncio.gather(
                # 不加 rating_min：高德基础 POI 多无评分(=0)，过滤会把景点全删光，
                # 导致确认后取不到第一站候选。质量由关键词「景点」本身保证。
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 景点"}, ctx),
                self.agents.call("navigation", "navigation.search_poi",
                                 {"keyword": f"{dest} 充电桩"}, ctx),
                self.agents.call("info", "info.weather", {"city": dest}, ctx),
                self.agents.call("info", "info.forecast", {"city": dest}, ctx),
                self.agents.call("charging-planner", "charging.plan",
                                 {"destination": dest}, ctx),
                return_exceptions=True,
            )
            # 第 0 个调用即"{dest} 景点"——其 POI 即第一站候选（区别于第 1 个"充电桩"）
            attractions = results[0] if results else None
            if (isinstance(attractions, AgentResult) and attractions.ui_card
                    and attractions.ui_card.get("type") == "poi_list"):
                first_pois = attractions.ui_card.get("items", []) or []
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

        # 缓存行程上下文：供「改第N天」局部重规划与确认后取第一站候选。
        # first_stop = 行程第一天的主要景点（如天坛公园），确认后据此搜 POI 设为导航第一站。
        self._remember(ctx.session_id, destination=dest, days=days, itinerary=plan,
                       pois=first_pois, first_stop=_first_stop_from_itinerary(plan))

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
        """修改已有行程（局部重规划，保留未提及的天数）。"""
        sid = ctx.session_id
        st = self._sessions.get(sid or "", {})

        # 确认轮 → 收尾（同 _plan，避免"确认→再改→再确认"死循环）
        if meta.get("confirmed") == "true":
            return await self._finalize(ctx, sid, st.get("destination", ""), st.get("days", ""))

        modification = intent.slots.get("modification", "").strip()
        if not modification:
            return AgentResult(
                status=NEED_SLOT, speech="您想怎么调整行程？",
                follow_up="例如：第二天换成宋城", missing_slots=["modification"])

        # LLM 局部重规划：带上原行程，只改用户提到的部分，未提及的天数原样保留。
        # 缺原行程上下文（如跨重启）才退化为仅按修改要求生成。
        prior = st.get("itinerary", "")
        prompt = (
            (f"原始行程：\n{prior}\n\n" if prior else "")
            + f"用户想修改：{modification}\n"
            + ("请在原始行程基础上，只改动用户明确提到的天/景点，未提及的部分必须原样保留，"
               "输出完整的修改后行程（按天列要点）。" if prior
               else f"原始需求：{intent.raw_text}\n请根据修改要求给出修改后的行程要点（只改提到的部分）。")
        )
        try:
            modified = await self.llm.complete([
                {"role": "system", "content": _MODIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ], temperature=0.7, max_tokens=400)
        except Exception:
            modified = f"已记录您的修改要求：{modification}。请稍后确认。"

        # 更新缓存行程 + 第一站（改后第一天可能变了，重新解析；保留 destination/days/pois）
        self._remember(sid, destination=st.get("destination", ""),
                       days=st.get("days", ""), itinerary=modified,
                       first_stop=_first_stop_from_itinerary(modified))

        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"{modified}\n\n确认按此调整吗？",
            follow_up="说『确认』即可",
        ).action("trip.modify", {"modification": modification}, require_confirm=True)
