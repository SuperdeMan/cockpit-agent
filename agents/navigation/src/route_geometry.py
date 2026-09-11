"""路线几何 → 卡片字段（2026-09-11，Android 地图页画路线；设计
`docs/design/2026-09-11-android-sheet-controls-map-tabletop.md` §3.2）。纯函数、零 I/O。

高德 `/v3/direction/driving?extensions=all` 每个 step 带 `polyline`（"lng,lat;lng,lat;…"）。
一条城内路线几千个点、JSON 化后几十 KB——卡片要进 WS 帧、进聊天记录持久化，
所以这里**等距抽样到 ≤ PATH_MAX_POINTS**（保头尾）并四舍五入到 5 位小数（约 1m）。
卡片上的坐标一律 `[lat, lng]`（与 `hmi/src/types.ts::RoutePlanCard.path` 一致）；
高德原串是 `lng,lat`，翻转只在这一处做。
"""
from __future__ import annotations

from typing import Any, Iterable

PATH_MAX_POINTS = 240


def parse_amap_polyline(polyline: str) -> list[list[float]]:
    """"lng,lat;lng,lat" → [[lat, lng], …]。空串 / 坏段跳过，不抛。"""
    out: list[list[float]] = []
    for seg in (polyline or "").split(";"):
        if "," not in seg:
            continue
        try:
            lng_s, lat_s = seg.split(",")[:2]
            lat, lng = float(lat_s), float(lng_s)
        except ValueError:
            continue
        if lat == 0 and lng == 0:
            continue
        out.append([lat, lng])
    return out


def sample_path(points: list[list[float]], cap: int = PATH_MAX_POINTS) -> list[list[float]]:
    """等距抽样到 ≤ cap 点，保头尾；不足 cap 原样返回。"""
    n = len(points)
    if n <= cap or cap < 2:
        return list(points)
    step = (n - 1) / (cap - 1)
    return [points[round(i * step)] for i in range(cap)]


def route_path_from_amap(path: dict[str, Any], cap: int = PATH_MAX_POINTS) -> list[list[float]]:
    """高德 driving path（含 steps[].polyline）→ 抽样后的 [[lat, lng], …]。
    相邻重复点（每步末点 = 下一步首点）去掉一份；抽样后四舍五入到 5 位。"""
    pts: list[list[float]] = []
    for s in (path or {}).get("steps") or []:
        for pt in parse_amap_polyline(str(s.get("polyline") or "")):
            if pts and pts[-1] == pt:
                continue
            pts.append(pt)
    return [[round(lat, 5), round(lng, 5)] for lat, lng in sample_path(pts, cap)]


def _loc(obj: Any) -> dict[str, float] | None:
    """带 lat/lng 属性或键的对象 → {"lat", "lng"}；缺任一 / 非数 / 0,0 → None。"""
    if obj is None:
        return None
    lat = getattr(obj, "lat", None) if not isinstance(obj, dict) else obj.get("lat")
    lng = getattr(obj, "lng", None) if not isinstance(obj, dict) else obj.get("lng")
    try:
        lat_f, lng_f = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if lat_f == 0 and lng_f == 0:
        return None
    return {"lat": round(lat_f, 6), "lng": round(lng_f, 6)}


def card_geometry(*, origin: Any = None, destination: Any = None,
                  waypoints: Iterable[Any] | None = None,
                  path: list[list[float]] | None = None) -> dict[str, Any]:
    """出 route_plan / charging_route 卡上的几何字段。**只写拿得到的**：
    起点 / 终点坐标缺席不写键，途经点没坐标只写 name/address，折线为空不写 path。
    客户端（mobile core/map/geometry.ts）据此决定给不给「查看路线」入口。"""
    out: dict[str, Any] = {}
    o = _loc(origin)
    if o:
        out["origin_loc"] = o
    d = _loc(destination)
    if d:
        out["destination_loc"] = d
    if waypoints is not None:
        wps = []
        for w in waypoints:
            entry: dict[str, Any] = {"name": getattr(w, "name", None) if not isinstance(w, dict) else w.get("name"),
                                     "address": getattr(w, "address", None) if not isinstance(w, dict) else w.get("address")}
            loc = _loc(w)
            if loc:
                entry.update(loc)
            wps.append(entry)
        out["waypoints"] = wps
    if path:
        out["path"] = [[float(a), float(b)] for a, b in path]
    return out
