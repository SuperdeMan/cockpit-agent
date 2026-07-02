"""trip-planner 目的地抽取（R2.1：原编排核心 planning._extract_trip 搬入 Agent）。

覆盖原 planning.py 里被删除的抽取/门控断言：目的地+天数+偏好解析、通勤/固定点 BLOCK、
无出行信号不判为行程。逐字迁移，行为与原 _extract_trip 一致。
"""
from agents.trip_planner.src.extract import extract_trip


def test_extracts_dest_days_prefs():
    dest, days, prefs = extract_trip("周末去杭州两天，带老人，不要太累，顺便看看天气")
    assert dest == "杭州"
    assert days == "2"
    assert "带老人" in prefs


def test_dest_before_days_form():
    dest, days, _ = extract_trip("杭州三日游")
    assert dest == "杭州"
    assert days == "3"


def test_pref_and_days_together():
    dest, days, prefs = extract_trip("去成都玩三天轻松点")
    assert dest == "成都"
    assert days == "3"
    assert "轻松" in prefs


def test_commute_and_fixed_points_blocked():
    for t in ("去公司三天", "去学校三天", "到机场三天"):
        assert extract_trip(t) == ("", "", ""), t


def test_dest_without_travel_signal_is_not_a_trip():
    # 有目的地但无 天数/偏好/日游/trigger → 非出行（避免把单点导航当行程）
    assert extract_trip("去三亚玩")[0] == ""


def test_signal_without_dest_returns_empty():
    assert extract_trip("两天后开会") == ("", "", "")
    assert extract_trip("帮我规划行程") == ("", "", "")


def test_empty_input():
    assert extract_trip("") == ("", "", "")
    assert extract_trip(None) == ("", "", "")
