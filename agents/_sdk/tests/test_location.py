"""坐标年龄判据（agents/_sdk/location.py，2026-09-17）。

真机在家问「我在哪」答出公司地址：客户端把几小时前的缓存当此刻上行，服务端照念。
年龄只在这里判一次：带时刻就算年龄，超过 STALE_LOCATION_S 不许当「此刻」；不带时刻（老客户端）按不旧。
"""
from agents._sdk.location import (
    STALE_LOCATION_S, current_location_from_meta, location_age_s, location_is_stale,
)

NOW_MS = 1_789_600_000_000


def test_coordinates_still_parse_without_a_timestamp():
    loc = current_location_from_meta({"current_lat": "22.54", "current_lng": "113.94"})
    assert loc is not None and loc.lat == 22.54 and loc.lng == 113.94 and loc.at_ms is None
    assert location_age_s({"current_lat": "22.54", "current_lng": "113.94"}, NOW_MS) is None
    assert location_is_stale({"current_lat": "22.54", "current_lng": "113.94"}, NOW_MS) is False


def test_age_is_computed_from_current_location_at():
    meta = {"current_lat": "22.54", "current_lng": "113.94", "current_location_at": str(NOW_MS - 90_000)}
    assert current_location_from_meta(meta).at_ms == NOW_MS - 90_000
    assert location_age_s(meta, NOW_MS) == 90.0
    assert location_is_stale(meta, NOW_MS) is False


def test_older_than_the_threshold_is_stale():
    meta = {"current_lat": "22.54", "current_lng": "113.94",
            "current_location_at": str(NOW_MS - (STALE_LOCATION_S + 1) * 1000)}
    assert location_is_stale(meta, NOW_MS) is True
    at_limit = {**meta, "current_location_at": str(NOW_MS - STALE_LOCATION_S * 1000)}
    assert location_is_stale(at_limit, NOW_MS) is False


def test_bad_or_future_timestamps_mean_unknown_age():
    base = {"current_lat": "22.54", "current_lng": "113.94"}
    assert location_age_s({**base, "current_location_at": "abc"}, NOW_MS) is None
    assert location_age_s({**base, "current_location_at": "0"}, NOW_MS) is None
    # 客户端墙钟略快：几秒的未来夹到 0，不算陈旧
    assert location_age_s({**base, "current_location_at": str(NOW_MS + 3_000)}, NOW_MS) == 0.0
    # 明显在未来（时钟乱了）：不知道多旧
    assert location_age_s({**base, "current_location_at": str(NOW_MS + 3_600_000)}, NOW_MS) is None
    assert location_is_stale({**base, "current_location_at": str(NOW_MS + 3_600_000)}, NOW_MS) is False
