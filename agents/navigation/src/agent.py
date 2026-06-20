"""导航 Agent —— 所有 Agent 的参考范本。

演示：意图分发、缺槽位追问(NEED_SLOT)、按引用取上下文(ctx.fetch)、
产出动作(action) 与 HMI 卡片(ui_card)。
Phase 1：使用 Provider 适配层（mock/real 可切换）。
"""
from __future__ import annotations
import json
import logging
import os

from agents._sdk import BaseAgent, AgentResult, NEED_SLOT, FAILED
from agents._sdk.http import ProviderError
from .providers import build_poi_provider
from .providers.base import GeoPoint
from .providers.mock import MockPOIProvider

logger = logging.getLogger("agent.navigation")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")


class NavigationAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.poi = build_poi_provider()
        self._fallback = MockPOIProvider()  # 真实 provider 抖动时的降级兜底

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "navigation.search_poi": self._search_poi,
            "navigation.navigate_to": self._navigate_to,
            "navigation.reverse_geocode": self._reverse_geocode,
            "navigation.poi_detail": self._poi_detail,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="抱歉，这个导航请求我还不会处理。")

    async def _search_poi(self, intent, ctx, meta) -> AgentResult:
        keyword = intent.slots.get("keyword") or intent.slots.get("category")
        if not keyword:
            return AgentResult(status=NEED_SLOT, speech="您想找什么类型的地点呢？",
                               follow_up="请提供搜索关键词，如『充电站』『川菜馆』")

        # 按引用取车辆当前位置（隐私最小化：只取需要的 scope）
        ctx_values = await ctx.fetch("vehicle.location")
        location_data = ctx_values.get("vehicle.location", "")
        near = None
        if isinstance(location_data, str) and location_data:
            near = GeoPoint(address=location_data)

        rating_min = float(intent.slots.get("rating_min", 0) or 0)
        # 真实 provider 失败（超时/熔断/厂商错误）降级到 mock，保证链路不阻断；
        # 失败本身已由 provider span(outcome=error) 记录，便于在 Dashboard 发现。
        try:
            results = await self.poi.search(keyword, near=near, rating_min=rating_min, meta=meta)
        except ProviderError as e:
            logger.warning("poi search failed, fallback to mock: %s", e)
            results = await self._fallback.search(keyword, near=near, rating_min=rating_min, meta=meta)
        resolved_keyword = keyword

        # Planner 有时会把“去深圳笋一样的建筑物”误抽成“笋岗”这类普通关键词。
        # 视觉地标描述即使碰巧命中一个同名普通 POI，也要优先由地图验证语义候选；
        # 候选名已含城市/正式名称，不能受车辆当前城市的周边检索范围限制。
        raw_text = (intent.raw_text or "").strip()
        is_visual_landmark = self._is_visual_landmark_description(raw_text)
        if raw_text and (not results or is_visual_landmark):
            for candidate in await self._landmark_candidates(raw_text):
                try:
                    candidate_results = await self.poi.search(
                        candidate,
                        near=None if is_visual_landmark else near,
                        rating_min=rating_min,
                        meta=meta,
                    )
                except ProviderError as e:
                    logger.warning("semantic POI candidate search failed: %s", e)
                    continue
                if candidate_results:
                    resolved_keyword, results = candidate, candidate_results
                    break

        items = [{"id": r.id, "name": r.name, "rating": r.rating,
                  "distance_km": r.distance_km, "address": r.address} for r in results]
        card = {"type": "poi_list", "keyword": resolved_keyword, "items": items}

        if results and self._is_navigation_phrase(raw_text):
            first = results[0]
            return AgentResult(
                speech=f"识别到您说的是{first.name}（{first.address}）。已为您规划路线。",
                ui_card=card, data={"items": items},
            ).action("navigate", {"destination": first.name, "lat": first.lat, "lng": first.lng})

        names = "、".join(r.name for r in results[:3])
        return AgentResult(
            speech=f"为您找到 {len(results)} 个{resolved_keyword}，推荐前三个：{names}。需要导航过去吗？",
            ui_card=card,
            data={"items": items},  # F3：结构化结果供编排 slot_refs 取值（如 s1.data.items.0.id）
            follow_up="可以说『导航去第一个』",
        )

    @staticmethod
    def _is_navigation_phrase(text: str) -> bool:
        return (text or "").strip().startswith(("导航", "去", "到", "带我去"))

    @classmethod
    def _is_visual_landmark_description(cls, text: str) -> bool:
        """仅将带明显视觉/地标描述的导航请求提升为语义候选优先。"""
        normalized = (text or "").strip()
        if not cls._is_navigation_phrase(normalized):
            return False
        markers = ("像", "一样", "造型", "船型", "笋", "建筑", "地标")
        return any(marker in normalized for marker in markers)

    async def _navigate_to(self, intent, ctx, meta) -> AgentResult:
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            # 槽位为空时，尝试用 raw_text 做模糊搜索（处理"导航到上海那个像船一样的建筑"）
            raw = (intent.raw_text or "").strip()
            # 去掉常见前缀动词，提取核心描述
            for prefix in ("导航到", "导航去", "导航", "带我去", "去", "到"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):].strip()
                    break
            if raw:
                dest = raw
        if not dest:
            return AgentResult(status=NEED_SLOT, speech="您要去哪里？", follow_up="请告诉我目的地")

        resolved_name, results = await self._find_destination(dest, meta)
        if results:
            first = results[0]
            items = [{"id": r.id, "name": r.name, "rating": r.rating,
                      "distance_km": r.distance_km, "address": r.address} for r in results]
            prefix = (f"识别到您说的是{first.name}。" if resolved_name != dest else "")
            return AgentResult(
                speech=f"{prefix}为您找到{first.name}（{first.address}）。已为您规划路线。",
                ui_card={"type": "poi_list", "keyword": resolved_name, "items": items},
                data={"items": items},
            ).action("navigate", {"destination": first.name, "lat": first.lat, "lng": first.lng})

        return AgentResult(
            status=NEED_SLOT,
            speech=f"暂时无法确定「{dest}」对应的具体地点。",
            follow_up="请补充城市、所在区域，或附近的地标，我再为您定位。",
            missing_slots=["destination"],
        )

    async def _find_destination(self, description: str, meta) -> tuple[str, list]:
        """先用原话检索，未命中时仅将经高德验证的语义候选作为目的地。"""
        try:
            results = await self.poi.search(description, limit=3, meta=meta)
        except ProviderError as e:
            logger.warning("destination POI search failed: %s", e)
            results = []
        if results:
            return description, results

        for candidate in await self._landmark_candidates(description):
            try:
                results = await self.poi.search(candidate, limit=3, meta=meta)
            except ProviderError as e:
                logger.warning("landmark candidate POI search failed: %s", e)
                continue
            if results:
                return candidate, results
        return "", []

    async def _landmark_candidates(self, description: str) -> list[str]:
        """把视觉化地标描述转换为少量正式 POI 候选，不接受模型直接导航。"""
        prompt = (
            "把用户的导航目的地描述转换为中国地图可检索的正式 POI 名称。\n"
            f"用户描述：{description}\n\n"
            "仅当你对真实地点有把握时给出候选；最多三个；不要解释、不要编造。"
            "严格只输出 JSON 字符串数组，例如：[\"深圳华润大厦\"]。"
        )
        try:
            raw = await self.llm.complete([
                {"role": "system", "content": "你是中国地标名称归一化器，只输出 JSON 数组。"},
                {"role": "user", "content": prompt},
            ], temperature=0.0, max_tokens=120)
        except Exception as e:
            logger.warning("landmark resolution unavailable: %s", e)
            return []

        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            raw = raw.rsplit("```", 1)[0].strip()
        start, end = raw.find("["), raw.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            values = json.loads(raw[start:end + 1])
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(values, list):
            return []

        candidates: list[str] = []
        for value in values:
            candidate = value.strip() if isinstance(value, str) else ""
            if candidate and candidate not in candidates and len(candidate) <= 80:
                candidates.append(candidate)
        return candidates[:3]

    async def _reverse_geocode(self, intent, ctx, meta) -> AgentResult:
        """逆地理编码：坐标 → 地址。"""
        lng_s = intent.slots.get("lng", "")
        lat_s = intent.slots.get("lat", "")
        if not lng_s or not lat_s:
            # 尝试用车辆位置
            ctx_values = await ctx.fetch("vehicle.location")
            loc = ctx_values.get("vehicle.location", "")
            if isinstance(loc, str) and loc:
                return AgentResult(speech=f"当前位置：{loc}",
                                   data={"address": loc})
            return AgentResult(status=NEED_SLOT, speech="请提供坐标或位置信息。",
                               missing_slots=["lng", "lat"])
        try:
            lng, lat = float(lng_s), float(lat_s)
        except ValueError:
            return AgentResult(status=FAILED, speech="坐标格式不正确。")
        try:
            pt = await self.poi.reverse_geocode(lng, lat, meta=meta)
        except ProviderError as e:
            logger.warning("reverse_geocode failed, fallback to mock: %s", e)
            pt = await self._fallback.reverse_geocode(lng, lat, meta=meta)
        speech = f"该位置位于{pt.address}。" if pt.address else "未能解析该位置的地址。"
        return AgentResult(speech=speech,
                           data={"address": pt.address, "lng": lng, "lat": lat})

    async def _poi_detail(self, intent, ctx, meta) -> AgentResult:
        """查询 POI 详情。"""
        poi_id = (intent.slots.get("poi_id") or "").strip()
        if not poi_id:
            return AgentResult(status=NEED_SLOT, speech="请提供地点 ID。",
                               missing_slots=["poi_id"])
        try:
            poi = await self.poi.poi_detail(poi_id, meta=meta)
        except ProviderError as e:
            logger.warning("poi_detail failed, fallback to mock: %s", e)
            poi = await self._fallback.poi_detail(poi_id, meta=meta)
        speech = f"{poi.name}，地址：{poi.address}。"
        if poi.rating:
            speech += f"评分{poi.rating}。"
        card = {"type": "poi_detail", "id": poi.id, "name": poi.name,
                "address": poi.address, "lat": poi.lat, "lng": poi.lng,
                "rating": poi.rating, "category": poi.category}
        return AgentResult(speech=speech, ui_card=card, data={"poi": card})
