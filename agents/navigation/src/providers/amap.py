"""高德地图 POIProvider 适配（Web 服务 API）。

凭证经 env(AMAP_KEY) 注入，绝不进代码/日志。任一调用失败抛 ProviderError，
Agent/工厂侧据此回退 mock，不击穿主链。

高德约定：坐标顺序为 ``"lng,lat"``（经度在前，经典坑）；HTTP 200 但 ``status!="1"``
即逻辑失败（看 ``info``/``infocode``，如 key 错为 10001）；空字段有时返回 ``[]``。
docs: https://lbs.amap.com/api/webservice/guide/api/georegeo （地理/逆地理编码）
"""
from __future__ import annotations
import logging

from agents._sdk.http import AsyncHttpClient, ProviderError
from .base import POIProvider, POI, GeoPoint

logger = logging.getLogger("agent.navigation.amap")

_BASE = "https://restapi.amap.com"


def _as_str(v) -> str:
    """高德空字段有时返回 []，统一成空串。"""
    if isinstance(v, list):
        return ""
    return str(v) if v is not None else ""


class AmapPOIProvider(POIProvider):
    def __init__(self, key: str, base_url: str = _BASE):
        if not key:
            raise ValueError("AMAP_KEY required for AmapPOIProvider")
        self._key = key
        self._base = base_url.rstrip("/")
        self._http = AsyncHttpClient(vendor="amap", service="navigation")

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

    async def geocode_level(self, address: str, meta=None) -> tuple[str, str]:
        """正向地理编码返回 (行政级别, "lng,lat")。

        R1（旅程 B3-2/B2-3 家族）：「导航去惠州/广州塔」这类短名，带 near 偏置的关键词
        搜索会把就近弱匹配顶上 top1（0.3km 的「惠州出口」）。高德 geocode 的 level 字段
        （国家/省/市/区县/…/兴趣点）是行政级别的权威判据。失败返回 ("", "")，调用方 fail-open。
        """
        try:
            data = await self._get("/v3/geocode/geo", {"address": address}, "geocode", meta)
        except ProviderError:
            return "", ""
        geocodes = data.get("geocodes") or []
        if not geocodes:
            return "", ""
        return _as_str(geocodes[0].get("level")), _as_str(geocodes[0].get("location"))

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

    def _poi_from(self, p: dict) -> POI:
        lng = lat = 0.0
        loc = _as_str(p.get("location"))
        if "," in loc:
            try:
                lng_s, lat_s = loc.split(",")[:2]
                lng, lat = float(lng_s), float(lat_s)
            except ValueError:
                pass
        try:
            rating = float((p.get("business") or {}).get("rating") or 0)
        except (ValueError, TypeError):
            rating = 0.0
        dist_km = 0.0
        dist = p.get("distance")
        if dist not in (None, "", []):
            try:
                dist_km = round(float(dist) / 1000, 1)
            except (ValueError, TypeError):
                dist_km = 0.0
        return POI(
            id=_as_str(p.get("id")), name=_as_str(p.get("name")),
            address=_as_str(p.get("address")), lat=lat, lng=lng,
            rating=rating, distance_km=dist_km, category=_as_str(p.get("type")),
        )

    async def search(self, keyword: str, near: GeoPoint = None, category: str = "",
                     rating_min: float = 0, limit: int = 5, page: int = 1,
                     meta: dict | None = None) -> list[POI]:
        loc = await self._resolve_location(near, meta)
        common = {"keywords": keyword,
                  "page_size": str(max(1, min(limit, 25))),
                  "page_num": str(max(1, page)),  # 翻页："换一批"取下一页不同结果
                  "show_fields": "business"}
        if loc:  # 有位置 → 周边搜索（带 distance）
            data = await self._get("/v5/place/around", {**common, "location": loc},
                                   "place_around", meta)
        else:     # 无位置 → 关键字检索
            data = await self._get("/v5/place/text", common, "place_text", meta)
        results: list[POI] = []
        for p in (data.get("pois") or []):
            poi = self._poi_from(p)
            if rating_min and poi.rating < float(rating_min):
                continue
            results.append(poi)
            if len(results) >= limit:
                break
        return results

    async def get_route(self, origin: GeoPoint, destination: GeoPoint,
                        meta: dict | None = None, with_polyline: bool = False,
                        waypoints: list[GeoPoint] | None = None) -> dict:
        o = await self._resolve_location(origin, meta)
        d = await self._resolve_location(destination, meta)
        if not o or not d:
            raise ProviderError("amap route: cannot resolve origin/destination")
        # with_polyline=True → extensions=all 返回逐步几何，供沿途取点（如充电途经点）；
        # 默认 base 更轻，不影响既有调用。
        ext = "all" if with_polyline else "base"
        params = {"origin": o, "destination": d, "extensions": ext}
        # waypoints：途经点（出发地→途经点→目的地），供路线规划卡算真实全程距离/时长
        if waypoints:
            wlocs = []
            for w in waypoints:
                wl = await self._resolve_location(w, meta)
                if wl:
                    wlocs.append(wl)
            if wlocs:
                params["waypoints"] = ";".join(wlocs)
        data = await self._get("/v3/direction/driving", params,
                               "direction_driving", meta)
        paths = (data.get("route") or {}).get("paths") or []
        if not paths:
            raise ProviderError("amap route: no path")
        path = paths[0]
        result = {
            "distance_km": round(float(path.get("distance") or 0) / 1000, 1),
            "duration_min": round(float(path.get("duration") or 0) / 60, 1),
            "steps": [_as_str(s.get("instruction"))
                      for s in (path.get("steps") or []) if s.get("instruction")],
        }
        if with_polyline:
            # 逐步累计里程，记录每步终点坐标——用于按"沿途第 N 公里"取一个途经坐标
            points, cum_m = [], 0.0
            for s in (path.get("steps") or []):
                cum_m += float(s.get("distance") or 0)
                poly = _as_str(s.get("polyline"))
                last = poly.split(";")[-1] if poly else ""
                if "," in last:
                    try:
                        lng_s, lat_s = last.split(",")[:2]
                        points.append({"lng": float(lng_s), "lat": float(lat_s),
                                       "cum_km": round(cum_m / 1000, 1)})
                    except ValueError:
                        pass
            result["points"] = points
        return result

    async def reverse_geocode(self, lng: float, lat: float,
                              meta: dict | None = None) -> GeoPoint:
        """逆地理编码：坐标 → 地址。高德 /v3/geocode/regeo。"""
        location = f"{lng},{lat}"
        data = await self._get("/v3/geocode/regeo",
                               {"location": location, "extensions": "base"},
                               "geocode_regeo", meta)
        regeocode = data.get("regeocode") or {}
        addr = _as_str(regeocode.get("formatted_address"))
        return GeoPoint(lat=lat, lng=lng, address=addr)

    async def poi_detail(self, poi_id: str,
                         meta: dict | None = None) -> POI:
        """查询 POI 详情。高德 /v5/place/detail。"""
        data = await self._get("/v5/place/detail",
                               {"id": poi_id, "show_fields": "business"},
                               "place_detail", meta)
        pois = data.get("pois") or []
        if not pois:
            raise ProviderError(f"amap poi_detail: no result for {poi_id}")
        return self._poi_from(pois[0])
