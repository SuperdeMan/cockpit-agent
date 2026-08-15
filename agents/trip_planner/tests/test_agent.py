"""trip-planner agent 级契约测试（R8 起）。既有流水线/模型测试见同目录其它文件。"""
from agents.trip_planner.src.agent import TripPlannerAgent

def test_modify_rainy_days_swapped_indoor():
    """R8（旅程 B3-1）：「哪天要下雨就换成室内的」——按 Day.weather 确定性定位雨天并
    点名「室内」；无雨行程诚实说不用调整。原路径把这句并进偏好整程重规划，LLM 软约束
    压不住、原样端回（真栈假重排）。"""
    import asyncio
    from agents._sdk.testing import make_context, run_handle
    from agents.trip_planner.src.models import Trip, Day, Stop

    agent = TripPlannerAgent()

    def _trip(rain_day1: bool) -> Trip:
        w1 = {"date": "2026-07-18", "text": "大雨" if rain_day1 else "多云",
              "temp_high": "30", "temp_low": "26"}
        return Trip(destination="珠海", days=2, itinerary=[
            Day(day_index=1, weather=w1,
                stops=[Stop(stop_id="s1", name="海滨泳场", type="attraction")]),
            Day(day_index=2, weather={"date": "2026-07-19", "text": "多云"},
                stops=[Stop(stop_id="s2", name="珠海渔女", type="attraction")]),
        ])

    kv = {}
    ctx = make_context()

    async def _save(key, value):
        kv[key] = value
        return True

    async def _load(key):
        return kv.get(key)

    ctx.save_shared_state = _save
    ctx.load_shared_state = _load

    # 无雨 → 诚实不动
    kv["trip_active"] = _trip(rain_day1=False).to_dict()
    res = asyncio.run(run_handle(
        agent, "trip.modify",
        slots={"modification": "哪天要下雨的话，把那天的安排换成室内的"},
        raw_text="哪天要下雨的话，把那天的安排换成室内的", ctx=ctx))
    assert "没有雨" in res.speech and "不用调整" in res.speech

    # 第1天大雨 → 话术点名室内 + 待确认
    kv["trip_active"] = _trip(rain_day1=True).to_dict()
    res = asyncio.run(run_handle(
        agent, "trip.modify",
        slots={"modification": "哪天要下雨的话，把那天的安排换成室内的"},
        raw_text="哪天要下雨的话，把那天的安排换成室内的", ctx=ctx))
    assert res.status == "need_confirm"
    assert "第1天" in res.speech and "室内" in res.speech

    # Planner 可能把原句扩写，雨/室内标记相距超过旧的 15 字窗口；仍须走同一确定性路径。
    kv["trip_active"] = _trip(rain_day1=True).to_dict()
    res = asyncio.run(run_handle(
        agent, "trip.modify",
        slots={"modification": (
            "把下雨那天的户外景点（珠海日月贝、珠海渔女、爱情邮局）换成室内活动"
        )},
        raw_text="哪天要下雨的话，把那天的安排换成室内的", ctx=ctx))
    assert res.status == "need_confirm"
    assert "第1天" in res.speech and "室内" in res.speech


# ── E3：点名 POI 混进城市序（真栈六城长句抓修）─────────────────────
def test_named_poi_is_dropped_from_the_city_sequence():
    """planner 把「大秋裤→东方之门」同时填进 destination 与 must_visit（真栈实测），
    「东方之门」于是成了一座城：逐城建池搜「东方之门 景点」、每天都标着它。"""
    from agents.trip_planner.src.agent import _drop_named_pois_from_cities as drop
    cities = ["苏州", "东方之门", "无锡", "南京", "济南", "潍坊", "北京"]
    mv = ["东方之门（大秋裤）", "灵山大佛", "长江大桥", "趵突泉", "潍坊风筝博物馆", "天安门"]

    assert drop(cities, mv) == ["苏州", "无锡", "南京", "济南", "潍坊", "北京"]


def test_city_named_inside_a_poi_name_is_not_dropped():
    """判据是**归一后精确相等**不是包含：「苏州园林」不能把「苏州」剔掉。"""
    from agents.trip_planner.src.agent import _drop_named_pois_from_cities as drop
    assert drop(["苏州", "杭州"], ["苏州园林", "西湖"]) == ["苏州", "杭州"]


def test_city_filter_is_a_noop_without_must_visit():
    from agents.trip_planner.src.agent import _drop_named_pois_from_cities as drop
    assert drop(["苏州", "杭州"], []) == ["苏州", "杭州"]
    assert drop(["苏州"], ["苏州"]) == ["苏州"]       # 单城不参与（<2 直接返回）


def test_unspecified_days_defaults_to_city_count_for_multi_city():
    """多城且用户没说天数 → 天数取城数（每城至少一天）。真栈六城 3 天实测：
    南京/济南/潍坊/北京四城一天都没分到，归城校正无处可搬。"""
    from agents.trip_planner.src.agent import _days_for_cities as fix
    six = ["苏州", "无锡", "南京", "济南", "潍坊", "北京"]
    assert fix("用户未指定", six) == "6"
    assert fix("", six) == "6"


def test_explicit_days_are_never_overridden():
    """用户明说了天数就照办——含**中文数字**（`_norm_days` 会把「三天」读成 0，
    拿它当判据就成了替用户改需求）。单城行程一律不参与。"""
    from agents.trip_planner.src.agent import _days_for_cities as fix
    six = ["苏州", "无锡", "南京", "济南", "潍坊", "北京"]
    for spoken in ("3", "3天", "三天", "两天"):
        assert fix(spoken, six) == spoken
    assert fix("用户未指定", ["杭州"]) == "用户未指定"
