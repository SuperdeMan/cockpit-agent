"""会话级当前位置解析。

精确坐标只来自已获浏览器授权的请求 ``meta``，不写入记忆或持久化存储。
调用方负责在入口处完成 ``location.read`` 权限校验；本模块只做格式与范围校验。

2026-09-17 起带**年龄**：客户端把定位产生的时刻放在 ``current_location_at``（ms 墙钟），
「我在哪 / 起点」这类把坐标当**此刻**位置念出去的能力，要先看它有多旧——
真机在家问「我在哪」答出公司地址，就是拿几小时前的缓存当此刻（复盘
docs/reviews/2026-09-16-android-e2e-latency-location-wait.md）。判据只在这里定一次。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

#: 超过这个年龄的坐标不许被当成「此刻」念出去，只能说成「N 分钟前的位置」
STALE_LOCATION_S = 10 * 60

#: 「本地」半径（km）：裸区县名的心智是本地（接地卡 D2），**名字对不上的弱匹配**也只在这个半径内才当目的地 / 搜索中心。
#: 评审四轮真栈（`bda5af71`，CL1 / RS33 / RS36）：深圳定位下「云岚国际中心」近侧 / 全国都只捞回北京的「云岚之境美容美体中心」，
#: 导航兜底照样当目的地、出发去 1940 km 外（E-1）；同一个不存在的地名交给周边检索，被 geocode 到云南宣威照搜不误（`8528df0b`
#: RS33，评审四轮待办）。导航与周边检索用同一个数，别各写一个。
LOCAL_RADIUS_KM = 150.0
#: 「没找到这个地点」的追问——导航、地点搜索、周边检索三条入口共用一句（探针按它判分支，`follow_up_any`）。
NOT_FOUND_FOLLOW_UP = "请补充城市、所在区域，或附近的地标，我再为您定位。"


def rough_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """等距圆柱近似的两点距离（判「本地」的 150km 阈值足够，不求测地精度）。"""
    dlat = (lat2 - lat1) * 111.0
    dlng = (lng2 - lng1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlng)
#: 允许的时钟前置（客户端墙钟略快于服务端），超出即当无效
_FUTURE_TOLERANCE_S = 5 * 60


@dataclass(frozen=True)
class CurrentLocation:
    lat: float
    lng: float
    #: 定位产生的墙钟时刻（ms）；客户端没给 / 给坏了 ⇒ None（当作不知道多旧）
    at_ms: int | None = None


def _at_ms_from_meta(meta: dict | None) -> int | None:
    raw = (meta or {}).get("current_location_at", "")
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def current_location_from_meta(meta: dict | None) -> CurrentLocation | None:
    """从请求 meta 解析合法的 WGS-84 纬经度，非法值一律忽略。"""
    try:
        lat = float((meta or {}).get("current_lat", ""))
        lng = float((meta or {}).get("current_lng", ""))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return CurrentLocation(lat=lat, lng=lng, at_ms=_at_ms_from_meta(meta))


def location_age_s(meta: dict | None, now_ms: int | None = None) -> float | None:
    """坐标的年龄（秒）。没带时刻 / 时刻坏了 / 明显在未来 ⇒ None（不知道多旧，调用方按「未知」处理）。"""
    at = _at_ms_from_meta(meta)
    if at is None:
        return None
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    age = (now - at) / 1000.0
    if age < -_FUTURE_TOLERANCE_S:
        return None
    return max(0.0, age)


def location_is_stale(meta: dict | None, now_ms: int | None = None) -> bool:
    """坐标是否旧到不能当「此刻」。年龄未知按**不旧**处理——老客户端不带时刻，行为不变。"""
    age = location_age_s(meta, now_ms)
    return age is not None and age > STALE_LOCATION_S
