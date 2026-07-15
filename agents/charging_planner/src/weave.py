"""充电编织纯函数 —— 沿真实路线几何按 SoC 放补电目标点。

抽自 `providers/amap.py:plan_route` 的滑点算法，做成**无 provider 依赖的纯函数**，
供 trip-planner 多日 leg 与 charging 自身复用（P0 先 trip-planner 用，charging 改用留后续清理）。

输入 `points` = 高德 `get_route(with_polyline=True)` 解析出的路线点 `[{lat,lng,cum_km}]`；
输出每个**需要补电的目标点**（at_km + 坐标），**不接地为具体充电站**——接地由调用方用 POI 搜索完成
（near=该坐标搜「充电站」），保持本函数纯粹可单测。续航足够或无路线点 → 返回空。
"""
from __future__ import annotations


def weave_charging_targets(points: list[dict], distance_km: float,
                           start_soc_pct: float, full_range_km: float,
                           *, first_leg_frac: float = 0.85, hop_frac: float = 0.65,
                           tail_buffer_km: float = 20.0,
                           max_stops: int = 4,
                           reserve_frac: float = 0.85) -> list[dict]:
    """沿路线按里程放补电目标点。

    策略（与原 amap.plan_route 一致）：首段用到 ~85% 当前可用续航再补，
    之后每段约 65% 满电续航补一次；末段 tail_buffer_km 内不再补（快到了）；最多 max_stops 次。

    返回 `[{"at_km": int, "lat": float, "lng": float}]`（按出现顺序）。
    直达判定带保留余量（Q2，旅程 A1-2 抓到：10%→50km 对 47.7km 判「足够直达」只剩
    2.3km 余量）：`distance_km <= usable * reserve_frac` 才算够，到达时至少留 15% 可用续航。
    无路线点或参数非法 → `[]`。
    """
    if not points or full_range_km <= 0 or distance_km <= 0:
        return []
    usable = max(0.0, start_soc_pct) / 100.0 * full_range_km
    if distance_km <= usable * reserve_frac:   # 足够直达且留有余量，无需补电
        return []

    targets: list[float] = []
    d = usable * first_leg_frac
    while d < distance_km - tail_buffer_km and len(targets) < max_stops:
        targets.append(d)
        d += full_range_km * hop_frac
    if not targets:
        # 短途但余量不足（首个目标点落进尾缓冲）：至少放一个补电点，夹到尾缓冲之前——
        # 否则空集在下游等同「直达」，Q2 的余量判定就被架空了。
        targets.append(max(1.0, min(usable * first_leg_frac,
                                    distance_km - tail_buffer_km)))

    out: list[dict] = []
    for t in targets:
        pt = next((p for p in points
                   if isinstance(p, dict) and (p.get("cum_km") or 0) >= t), None)
        if not pt:
            continue
        out.append({"at_km": round(t),
                    "lat": pt.get("lat"), "lng": pt.get("lng")})
    return out
