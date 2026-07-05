"""高德 PlaceProvider 适配（Web 服务 API，POI 2.0）——富数据周边搜索 + 详情增强。

沿用 navigation 已验证的高德约定：坐标 "lng,lat"（经度在前）；HTTP 200 但 status!="1" 即业务失败；
空字段常返回 []。本 provider 比 navigation 薄 POIProvider 多取 business.cost/tel/opentime/tag +
photos，供「这家怎么样/几点关门/人均多少」类详情。凭证经 env(AMAP_KEY)，绝不进代码/日志。

失败抛 ProviderError，Agent 侧据此降级 mock，不击穿主链。
docs: https://lbs.amap.com/api/webservice/guide/api/newpoisearch （POI 2.0 place around/text/detail）
"""
from __future__ import annotations
import logging
import re

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import PlaceProvider, Place, GeoPoint

logger = logging.getLogger("agent.nearby.amap")

_BASE = "https://restapi.amap.com"
# 富字段：business(评分/人均/电话/营业时间/标签/商圈) + photos(图片)
_SHOW_FIELDS = "business,photos"


def _as_str(v) -> str:
    """高德空字段有时返回 []，统一成空串。"""
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class AmapPlaceProvider(PlaceProvider):
    def __init__(self, key: str, base_url: str = _BASE):
        if not key:
            raise ValueError("AMAP_KEY required for AmapPlaceProvider")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="amap", service="nearby")

    async def _get(self, path: str, params: dict, op: str, meta) -> dict:
        data = await self._http.get_json(
            f"{self._base}{path}", params={**params, "key": self._key}, op=op, meta=meta)
        if str(data.get("status")) != "1":
            raise ProviderError(
                f"amap {op} failed: {data.get('info', 'unknown')} ({data.get('infocode', '')})")
        return data

    async def _geocode(self, address: str, meta) -> str | None:
        """地址 → "lng,lat"。无结果返回 None。"""
        data = await self._get("/v3/geocode/geo", {"address": address}, "geocode", meta)
        geocodes = data.get("geocodes") or []
        return (_as_str(geocodes[0].get("location")) or None) if geocodes else None

    async def _resolve_location(self, point: GeoPoint | None, meta) -> str | None:
        """把 GeoPoint 归一成高德的 "lng,lat"；地名经地理编码解析。"""
        if point is None:
            return None
        if point.lng and point.lat:
            return f"{point.lng},{point.lat}"
        addr = (point.address or "").strip()
        if not addr:
            return None
        parts = addr.split(",")
        if len(parts) == 2:  # 已是 "lng,lat"
            try:
                float(parts[0]); float(parts[1])
                return addr
            except ValueError:
                pass
        return await self._geocode(addr, meta)

    @staticmethod
    def _parse_cost(cost: str) -> float:
        """人均字符串 → 数值（"88"/"人均￥88"/"80-120" 取首个数）。解析不出返回 0（不过滤）。"""
        m = re.search(r"\d+", cost or "")
        return float(m.group()) if m else 0.0

    def _place_from(self, p: dict) -> Place:
        lng = lat = 0.0
        loc = _as_str(p.get("location"))
        if "," in loc:
            try:
                lng_s, lat_s = loc.split(",")[:2]
                lng, lat = float(lng_s), float(lat_s)
            except ValueError:
                pass
        biz = p.get("business") or {}
        try:
            rating = float(_as_str(biz.get("rating")) or 0)
        except (ValueError, TypeError):
            rating = 0.0
        dist_km = 0.0
        dist = p.get("distance")
        if dist not in (None, "", []):
            try:
                dist_km = round(float(dist) / 1000, 1)
            except (ValueError, TypeError):
                dist_km = 0.0
        photos = []
        for ph in (p.get("photos") or []):
            url = _as_str(ph.get("url")) if isinstance(ph, dict) else ""
            if url:
                photos.append(url)
        return Place(
            id=_as_str(p.get("id")), name=_as_str(p.get("name")),
            category=_as_str(p.get("type")), address=_as_str(p.get("address")),
            lat=lat, lng=lng, distance_km=dist_km, rating=rating,
            cost=_as_str(biz.get("cost")), tel=_as_str(biz.get("tel")),
            open_today=_as_str(biz.get("opentime_today")),
            open_week=_as_str(biz.get("opentime_week")),
            tags=_as_str(biz.get("tag")), area=_as_str(biz.get("business_area")),
            photos=photos,
        )

    async def search(self, keyword, *, category="", near=None, rating_min=0,
                     price_max=0, brand="", open_now=False, sort="", limit=10, page=1,
                     meta=None) -> list[Place]:
        loc = await self._resolve_location(near, meta)
        common = {"keywords": keyword,
                  # 多取一些余量供客户端过滤（评分/人均）后仍够 limit
                  "page_size": str(max(1, min(limit * 2, 25))),
                  "page_num": str(max(1, page)),
                  "show_fields": _SHOW_FIELDS}
        if loc:  # 有位置 → 周边搜索（带 distance）
            data = await self._get("/v5/place/around", {**common, "location": loc},
                                   "place_around", meta)
        else:    # 无位置 → 关键字检索
            data = await self._get("/v5/place/text", common, "place_text", meta)
        results: list[Place] = []
        for p in (data.get("pois") or []):
            place = self._place_from(p)
            if rating_min and place.rating < float(rating_min):
                continue
            if price_max and place.cost and self._parse_cost(place.cost) > float(price_max):
                continue
            # open_now 精确过滤（解析 opentime_today）留 P1；此处接受参数不过滤，避免误剔。
            results.append(place)
            if len(results) >= limit:
                break
        if sort == "rating":
            results.sort(key=lambda x: x.rating, reverse=True)
        return results

    async def detail(self, place_id="", *, name="", near=None, meta=None) -> Place:
        if not place_id:
            if not name:
                raise ProviderError("amap detail: need place_id or name")
            found = await self.search(name, near=near, limit=1, meta=meta)
            if not found:
                raise ProviderError(f"amap detail: no result for name={name}")
            if not found[0].id:
                return found[0]  # 无 id 直接返回搜索结果（已含富字段）
            place_id = found[0].id
        data = await self._get("/v5/place/detail",
                               {"id": place_id, "show_fields": _SHOW_FIELDS},
                               "place_detail", meta)
        pois = data.get("pois") or []
        if not pois:
            raise ProviderError(f"amap detail: no result for {place_id}")
        return self._place_from(pois[0])
