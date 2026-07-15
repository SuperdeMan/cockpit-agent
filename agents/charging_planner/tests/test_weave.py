"""充电编织纯函数单测。"""
from agents.charging_planner.src.weave import weave_charging_targets


def _route(distance_km: float, step_km: float = 10.0) -> list[dict]:
    """构造一条沿东向的等距路线点（cum_km 递增）。"""
    pts, cum = [], 0.0
    lat, lng = 30.0, 120.0
    while cum <= distance_km:
        pts.append({"lat": lat, "lng": lng + cum / 100.0, "cum_km": round(cum, 1)})
        cum += step_km
    return pts


def test_sufficient_range_no_charge():
    """续航足够直达 → 不放补电点。"""
    pts = _route(300)
    assert weave_charging_targets(pts, 300, start_soc_pct=80, full_range_km=500) == []


def test_direct_needs_reserve_margin():
    """Q2（旅程 A1-2）：10%→50km 对 47.7km 不算「足够直达」——到达须留 ≥15% 可用续航；
    且短途尾缓冲不能吞掉唯一补电点（空集在下游等同直达）。"""
    pts = _route(47.7, step_km=2)
    out = weave_charging_targets(pts, 47.7, start_soc_pct=10, full_range_km=500)
    assert out, "2.3km 余量不该判直达"
    assert out[0]["at_km"] <= 47.7 - 20 + 2      # 补电点被夹到尾缓冲之前（±路线点步长）
    # 余量充足（12%→60km，51≥47.7）才直达
    assert weave_charging_targets(pts, 47.7, start_soc_pct=12, full_range_km=500) == []


def test_long_trip_inserts_stops():
    """长途超续航 → 沿途按里程放补电目标点。"""
    pts = _route(1200, step_km=10)
    out = weave_charging_targets(pts, 1200, start_soc_pct=80, full_range_km=500)
    assert len(out) >= 1
    # 首点约在 80%*500*0.85=340km 处（取 >= 该里程的首个路线点）
    assert 330 <= out[0]["at_km"] <= 360
    assert out[0]["lat"] is not None and out[0]["lng"] is not None
    # at_km 单调递增、不越界
    kms = [s["at_km"] for s in out]
    assert kms == sorted(kms)
    assert all(k < 1200 for k in kms)


def test_low_soc_more_stops():
    """低电量长途 → 更早补、补更多次（但受 max_stops 上限）。"""
    pts = _route(1500, step_km=10)
    out = weave_charging_targets(pts, 1500, start_soc_pct=30, full_range_km=500)
    assert 1 <= len(out) <= 4
    assert out[0]["at_km"] <= 200          # 30%*500*0.85≈127km 先补


def test_empty_points_returns_empty():
    assert weave_charging_targets([], 1000, start_soc_pct=50, full_range_km=500) == []


def test_max_stops_capped():
    pts = _route(6000, step_km=20)
    out = weave_charging_targets(pts, 6000, start_soc_pct=50, full_range_km=400, max_stops=4)
    assert len(out) <= 4
