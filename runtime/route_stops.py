"""路线停靠点「是不是同一处」的判据（2026-10-10 真栈 swap2）——三处共用这一份：

- 编排合并本轮途经点（`orchestrator/cloud/waypoints.merged_waypoints`）：同一处只留一个；
- 导航改路线加站 / 换站（`agents/navigation`）：路线上已有的不再加、不当新站；
- 充电找「再加一个」（`agents/charging_planner`）：路线上已有的站不再推荐。

修前三处各认各的：充电步推荐了路线上已有的那个站，改路线那一步按名字把它又加了一遍，聚合再并一遍 ⇒
一条路线上三个一模一样的途经点。
"""
from __future__ import annotations

import math

#: 坐标相距不到这么远（公里）就是同一处：高德同一个 POI 在不同接口里的坐标差在几十米内
SAME_SPOT_KM = 0.05
#: 同名且相距不到这么远（公里）也是同一处；同名但更远是同一品牌的另一家（连锁站名常常一模一样）
SAME_NAME_KM = 0.5


def _field(stop, key: str):
    """dict（途经点）与对象（搜索结果）都认。"""
    return stop.get(key) if isinstance(stop, dict) else getattr(stop, key, None)


def plain_name(name) -> str:
    """比名字前统一全 / 半角括号、去空格：同一个站在搜索结果和模型改写里括号写法不一样。"""
    return str(name or "").replace("（", "(").replace("）", ")").replace(" ", "").strip()


def _coords(stop) -> tuple[float, float] | None:
    try:
        lat, lng = float(_field(stop, "lat")), float(_field(stop, "lng"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return lat, lng


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """等距圆柱近似（几百米尺度足够，不求测地精度）。"""
    dlat = (b[0] - a[0]) * 111.0
    dlng = (b[1] - a[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlng)


def same_stop(a, b) -> bool:
    """两个停靠点是不是同一处（带 name / lat / lng 的 dict 或对象）。

    两边都有坐标：相距不到 50 米，或同名且不到 500 米；有一边缺坐标：名字一致才算（空名字不算）。
    """
    name_a, name_b = plain_name(_field(a, "name")), plain_name(_field(b, "name"))
    same_name = bool(name_a) and name_a == name_b
    ca, cb = _coords(a), _coords(b)
    if ca and cb:
        km = _km(ca, cb)
        return km < SAME_SPOT_KM or (same_name and km < SAME_NAME_KM)
    return same_name


def on_route(stop, waypoints) -> bool:
    """这个点是不是路线上已有的某个途经点。"""
    return any(same_stop(stop, w) for w in waypoints or [])
