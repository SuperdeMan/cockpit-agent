"""会话级当前位置解析。

精确坐标只来自已获浏览器授权的请求 ``meta``，不写入记忆或持久化存储。
调用方负责在入口处完成 ``location.read`` 权限校验；本模块只做格式与范围校验。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurrentLocation:
    lat: float
    lng: float


def current_location_from_meta(meta: dict | None) -> CurrentLocation | None:
    """从请求 meta 解析合法的 WGS-84 纬经度，非法值一律忽略。"""
    try:
        lat = float((meta or {}).get("current_lat", ""))
        lng = float((meta or {}).get("current_lng", ""))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return CurrentLocation(lat=lat, lng=lng)
