"""周边检索的中心接地：名字对不上的地名，不拿别的城市冒充「附近」（评审四轮待办，2026-09-25）。

真栈 `8528df0b` RS33 第 2 趟：「第二个」的计划回落到掉档轮抢救出的 `nearby.search {category: 咖啡店, location: 云岚国际中心}`
（地点取自旧澄清的对象），这个不存在的地名交给无城市偏置的 geocode，落到云南宣威，照样播「为您找到 10 家咖啡厅，推荐：瑞幸咖啡
(宣威恒泰城店)…」。与导航 E-1 同一类（`agents._sdk.location.LOCAL_RADIUS_KM`，两处同一个数）：落在本地半径内照旧；半径外只有
用户说出了那座城才算数；geocode 没有结果时修前 provider 退成全国关键字检索，同样不许。

抢救回落本身不动：collector 全量 4498 次规划里回落 137 次，重试轮说「需要澄清」的 26 次里抢救计划对的约 24 次（「把全车门解锁」
「取消导航」这类，重试轮的澄清反而是错的）——错的这一次，后果落在这里。
"""
from __future__ import annotations

import asyncio

from agents._sdk.location import LOCAL_RADIUS_KM, NOT_FOUND_FOLLOW_UP
from agents._sdk.testing import run_handle
from agents.nearby.src.agent import NearbyAgent, _names_the_area
from agents.nearby.src.providers.base import GeocodeHit, GeoPoint, Place

_SHENZHEN = {"current_lat": "22.5410", "current_lng": "114.0579"}
_XUANWEI = GeocodeHit(lat=26.219, lng=104.104, province="云南省", city="曲靖市", district="宣威市")
_DONGGUAN = GeocodeHit(lat=22.970, lng=113.750, province="广东省", city="东莞市", district="")
_BEIJING = GeocodeHit(lat=39.934, lng=116.455, province="北京市", city="北京市", district="朝阳区")


class _Place:
    """偏置搜索按名字搜不到（或搜到的名字对不上）；geocode 按脚本回。记下每一次调用。"""

    def __init__(self, geocoded, *, verified: str = ""):
        self.geocoded = geocoded
        self.verified = verified
        self.searches: list[tuple[str, object]] = []
        self.geocodes: list[str] = []

    async def search(self, keyword, near=None, meta=None, **kw):
        self.searches.append((keyword, near))
        if self.verified and keyword == self.verified:
            return [Place(id="v", name=f"{keyword}广场", lat=22.53, lng=113.95)]
        if len(self.searches) == 1:
            return [Place(id="x", name="不相干的店", lat=22.60, lng=114.10)]    # 名字对不上
        return [Place(id="c1", name="某咖啡", lat=near.lat if near else 0, lng=near.lng if near else 0,
                      rating=4.5)]

    async def geocode(self, address, *, meta=None):
        self.geocodes.append(address)
        return self.geocoded


def _run(place, slots, raw, meta=_SHENZHEN, intent="nearby.search"):
    agent = NearbyAgent()
    agent.place = place
    return asyncio.run(run_handle(agent, intent, slots=slots, raw_text=raw, meta=meta))


# ── 修前的真栈形态 ────────────────────────────────────────────────────────────────

def test_rs33_a_place_geocoded_to_another_city_is_not_searched():
    place = _Place(_XUANWEI)
    res = _run(place, {"category": "咖啡店", "location": "云岚国际中心"}, "第二个")
    assert res.speech == "没找到「云岚国际中心」。"
    assert res.follow_up == NOT_FOUND_FOLLOW_UP
    assert res.data == {"items": [], "center": "unlocated"}
    assert [k for k, _ in place.searches] == ["云岚国际中心"]      # 只有解析中心那一次，没在宣威搜咖啡
    assert place.geocodes == ["云岚国际中心"]


def test_a_place_that_cannot_be_geocoded_is_not_searched_nationwide():
    place = _Place(None)
    res = _run(place, {"category": "咖啡店"}, "云岚国际中心附近的咖啡店")
    assert res.speech == "没找到「云岚国际中心」。" and res.data["center"] == "unlocated"
    assert len(place.searches) == 1


def test_the_indoor_fanout_takes_the_same_gate():
    place = _Place(_XUANWEI)
    res = _run(place, {"category": "室内", "location": "云岚国际中心"}, "云岚国际中心附近有什么室内玩的")
    assert res.speech == "没找到「云岚国际中心」。"
    assert len(place.searches) == 1


# ── 对照：修前就对的照旧 ──────────────────────────────────────────────────────────

def test_a_place_within_the_local_radius_is_searched_around_its_geocode():
    place = _Place(_DONGGUAN)
    res = _run(place, {"category": "咖啡店", "location": "松山湖"}, "松山湖附近的咖啡店")
    assert res.data["center"] == "slot" and res.data["items"]
    final_near = place.searches[-1][1]
    assert isinstance(final_near, GeoPoint) and abs(final_near.lat - _DONGGUAN.lat) < 1e-6


def test_a_far_city_the_user_named_is_searched():
    place = _Place(_BEIJING)
    res = _run(place, {"category": "咖啡店", "location": "北京三里屯"}, "北京三里屯附近的咖啡店")
    assert res.data["items"]
    assert abs(place.searches[-1][1].lat - _BEIJING.lat) < 1e-6


def test_a_verified_name_never_needs_the_geocode():
    place = _Place(_XUANWEI, verified="科技园")
    res = _run(place, {"category": "咖啡店", "location": "科技园"}, "科技园附近的咖啡店")
    assert res.data["items"] and place.geocodes == []
    assert abs(place.searches[-1][1].lat - 22.53) < 1e-6


def test_without_a_vehicle_position_the_old_path_stands():
    """判不了远近 ⇒ 照旧把地名交给 provider（不猜、也不拒）。"""
    place = _Place(_XUANWEI)
    res = _run(place, {"category": "咖啡店", "location": "云岚国际中心"}, "第二个", meta={})
    assert place.geocodes == [] and res.data.get("center") != "unlocated"
    assert place.searches[-1][1].address == "云岚国际中心"


def test_a_provider_without_geocoding_keeps_the_old_path():
    class _NoGeocode:
        def __init__(self):
            self.calls = []

        async def search(self, keyword, near=None, meta=None, **kw):
            self.calls.append((keyword, near))
            return []

    place = _NoGeocode()
    res = _run(place, {"category": "咖啡店", "location": "云岚国际中心"}, "第二个")
    assert res.speech != "没找到「云岚国际中心」。"
    assert len(place.calls) == 2 and place.calls[-1][1].address == "云岚国际中心"


# ── 判据本身 ─────────────────────────────────────────────────────────────────────

def test_names_the_area():
    assert _names_the_area(_BEIJING, "北京三里屯 北京三里屯附近的咖啡店")
    assert _names_the_area(_XUANWEI, "宣威火车站附近")
    assert _names_the_area(_XUANWEI, "去云南那边的咖啡店")
    assert not _names_the_area(_XUANWEI, "云岚国际中心 第二个")
    assert not _names_the_area(GeocodeHit(lat=0, lng=0, province="市"), "市中心")


def test_the_radius_is_the_navigation_one():
    from agents.navigation.src import agent as navigation
    assert navigation._LOCAL_RADIUS_KM == LOCAL_RADIUS_KM == 150.0
    assert navigation._NOT_FOUND_FOLLOW_UP == NOT_FOUND_FOLLOW_UP
