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


# ── C7（2026-08-28，QA P1-09）：修改的三条不变量 + 按天读 ────────────────
# 这四组量的都是「没被点名的那一维有没有被悄悄改掉」。真栈原样是：一句
# 「不要把珠海排到广州前面」→ 3 天变 4 天 + 跨城混排 + 还要用户确认。

def _pytest_trip(days=3, cities=("深圳", "广州", "珠海")):
    from agents.trip_planner.src.models import Trip, Day, Stop
    return Trip(destination="深圳、广州、珠海", days=days, cities=list(cities),
                itinerary=[
                    Day(day_index=i + 1, city=c,
                        stops=[Stop(stop_id=f"s{i}", name=f"{c}景点{i}",
                                    type="attraction", grounded=True,
                                    poi={"lat": 22.5 + i, "lng": 113.9})])
                    for i, c in enumerate(cities)])


def test_satisfied_order_constraint_is_answered_without_replanning():
    """C7-C：约束已经成立 ⇒ 零重规划、零确认、直答。"""
    from agents.trip_planner.src.agent import TripPlannerAgent as A
    trip = _pytest_trip()
    said = A._order_constraint_satisfied(trip, "不要把珠海排到广州前面")
    assert "广州在珠海前面" in said and "没动您的方案" in said


def test_unsatisfied_order_constraint_falls_through():
    """反向：约束**没**成立时必须让路走正常修改，不许拿一句话把它糊过去。"""
    from agents.trip_planner.src.agent import TripPlannerAgent as A
    trip = _pytest_trip(cities=("深圳", "珠海", "广州"))
    assert A._order_constraint_satisfied(trip, "不要把珠海排到广州前面") == ""


def test_order_constraint_needs_two_known_cities():
    """判据零领域词：城市名只从 `trip.cities` 解析，认不出就让路。"""
    from agents.trip_planner.src.agent import TripPlannerAgent as A
    trip = _pytest_trip()
    assert A._order_constraint_satisfied(trip, "不要把佛山排到广州前面") == ""
    assert A._order_constraint_satisfied(trip, "第二天换成宋城") == ""
    assert A._order_constraint_satisfied(_pytest_trip(cities=("杭州",)),
                                         "不要把珠海排到广州前面") == ""


def test_day_drift_is_disclosed_when_the_user_never_asked_for_it():
    """C7-B：天数被改动且用户没提过天数 ⇒ 确认话术里必须说出来。"""
    from agents.trip_planner.src.agent import TripPlannerAgent as A
    note = A._days_drift_note(3, _pytest_trip(days=4), "不要把珠海排到广州前面")
    assert "3天" in note and "4天" in note and "放不下" in note


def test_day_drift_is_silent_when_the_user_asked_for_it():
    """对照：用户自己说了天数就不是漂移，是要求——不许再警告一遍。"""
    from agents.trip_planner.src.agent import TripPlannerAgent as A
    assert A._days_drift_note(3, _pytest_trip(days=4), "改成四天") == ""
    assert A._days_drift_note(3, _pytest_trip(days=3), "随便调调") == ""
    # 「第N天」是定位不是天数——负向后顾要真的挡住它
    assert A._days_drift_note(3, _pytest_trip(days=4), "第二天换成宋城") != ""


def test_status_reads_a_named_day():
    """C7-D：「第二天有哪些安排」答那一天的站，不再答游标进度。"""
    import asyncio
    from agents._sdk.testing import make_context, run_handle
    agent = TripPlannerAgent()
    kv = {"trip_active": _pytest_trip().to_dict()}
    ctx = make_context()

    async def _save(key, value):
        kv[key] = value
        return True

    async def _load(key):
        return kv.get(key)

    ctx.save_shared_state, ctx.load_shared_state = _save, _load
    res = asyncio.run(run_handle(agent, "trip.status", slots={"day": "2"},
                                 raw_text="第二天有哪些安排", ctx=ctx))
    assert "第2天" in res.speech and "广州景点1" in res.speech
    assert res.data["day"] == 2
    # 槽空时从原话兜底
    res2 = asyncio.run(run_handle(agent, "trip.status", slots={},
                                  raw_text="第三天去哪几个地方", ctx=ctx))
    assert "第3天" in res2.speech and "珠海景点2" in res2.speech
    # 不存在的天要如实说，不得退回整程进度冒充回答
    res3 = asyncio.run(run_handle(agent, "trip.status", slots={"day": "9"},
                                  raw_text="第九天有哪些安排", ctx=ctx))
    assert "没有第9天" in res3.speech
    # 对照：不问天时行为逐字照旧（游标进度）
    res4 = asyncio.run(run_handle(agent, "trip.status", slots={},
                                  raw_text="行程到哪了", ctx=ctx))
    assert "共3站" in res4.speech


# ── C7-B：路径③（整程重规划）此前零测试覆盖 ─────────────────────────
# 方案点名要补的两条都在这里：**参数保全**（cities/theme/must_visit 不许丢）
# 与**天数守恒**（没提天数就不许自己变，变了要么回炉、要么显式说出来）。

def _agent_with_stub_pipeline(days_seq):
    """把 `_run_pipeline` 换成记账替身：按次序吐指定天数的 Trip，并录下每次入参。"""
    import asyncio  # noqa: F401  (调用方用)
    agent = TripPlannerAgent()
    calls = []
    seq = list(days_seq)

    async def _stub(ctx, meta, dest, days, prefs, raw_text, theme="",
                    cities=None, must_visit=None, near=None):
        calls.append({"dest": dest, "days": days, "prefs": prefs,
                      "theme": theme, "cities": list(cities or []),
                      "must_visit": list(must_visit or []), "near": near})
        out = _pytest_trip(days=seq.pop(0) if seq else 3)
        return out, {}

    agent._run_pipeline = _stub
    return agent, calls


def _ctx_with_trip(trip):
    from agents._sdk.testing import make_context
    kv = {"trip_active": trip.to_dict()}
    ctx = make_context()

    async def _save(key, value):
        kv[key] = value
        return True

    async def _load(key):
        return kv.get(key)

    ctx.save_shared_state, ctx.load_shared_state = _save, _load
    return ctx, kv


def test_whole_replan_carries_cities_theme_and_must_visit():
    """丢上下文是纯 bug：路径③此前只传 6 个位置参数，多城逐城建池当场退化。"""
    import asyncio
    from agents._sdk.testing import run_handle
    trip = _pytest_trip()
    trip.theme = "太平年"
    trip.must_visit = ["东方之门"]
    agent, calls = _agent_with_stub_pipeline([3])
    ctx, _kv = _ctx_with_trip(trip)
    asyncio.run(run_handle(agent, "trip.modify",
                           slots={"modification": "整体轻松一点"},
                           raw_text="整体轻松一点", ctx=ctx))
    assert calls, "路径③应被走到"
    assert calls[0]["cities"] == ["深圳", "广州", "珠海"]
    assert calls[0]["theme"] == "太平年"
    assert calls[0]["must_visit"] == ["东方之门"]


def test_whole_replan_retries_once_to_keep_the_day_count():
    """天数守恒：第一趟吐 4 天 ⇒ 回炉一次并把「保持3天不变」写进上下文。"""
    import asyncio
    from agents._sdk.testing import run_handle
    agent, calls = _agent_with_stub_pipeline([4, 3])
    ctx, _kv = _ctx_with_trip(_pytest_trip())
    res = asyncio.run(run_handle(agent, "trip.modify",
                                 slots={"modification": "整体轻松一点"},
                                 raw_text="整体轻松一点", ctx=ctx))
    assert len(calls) == 2, "应回炉一次"
    assert "保持3天不变" in calls[1]["prefs"]
    assert "会变成" not in res.speech        # 回炉成功 ⇒ 不需要再让用户裁决


def test_whole_replan_discloses_the_drift_when_the_retry_also_fails():
    """回炉还不等 ⇒ 不再硬掰，**把静默扩天变成显式选择**。"""
    import asyncio
    from agents._sdk.testing import run_handle
    agent, calls = _agent_with_stub_pipeline([4, 4])
    ctx, _kv = _ctx_with_trip(_pytest_trip())
    res = asyncio.run(run_handle(agent, "trip.modify",
                                 slots={"modification": "整体轻松一点"},
                                 raw_text="整体轻松一点", ctx=ctx))
    assert len(calls) == 2
    assert "原来的3天放不下" in res.speech and "会变成4天" in res.speech
    assert res.status == "need_confirm"


def test_whole_replan_does_not_fight_an_explicit_day_change():
    """对照：用户自己说「改成四天」时不回炉、不警告——那是要求不是漂移。"""
    import asyncio
    from agents._sdk.testing import run_handle
    agent, calls = _agent_with_stub_pipeline([4])
    ctx, _kv = _ctx_with_trip(_pytest_trip())
    res = asyncio.run(run_handle(agent, "trip.modify",
                                 slots={"modification": "改成四天"},
                                 raw_text="改成四天", ctx=ctx))
    assert len(calls) == 1
    assert "会变成" not in res.speech


# ── C7-A：接地的城市锚（「万象城 美食」命中杭州）────────────────────
def _poi(name, address, lat, lng):
    from agents.navigation.src.providers.base import POI
    return POI(id=name, name=name, address=address, lat=lat, lng=lng)


def _anchored_agent(found, level=("", "")):
    agent = TripPlannerAgent()

    async def _search(keyword, near=None, limit=8, meta=None, **kw):
        return list(found)

    async def _level(address, meta=None):
        return level

    agent.poi.search = _search
    agent.poi.geocode_level = _level
    return agent


_HERE = {"current_lat": "22.54", "current_lng": "113.93"}      # 深圳


def test_bare_poi_name_gets_the_current_city_as_anchor():
    """同城命中 ⇒ 把当前位置当锚传给建池，不再走全国序。"""
    import asyncio
    agent = _anchored_agent([_poi("深圳万象城", "深圳市罗湖区", 22.55, 114.09)])
    near, disclosure = asyncio.run(agent._city_anchor("万象城", [], _HERE))
    assert disclosure is None
    assert near is not None and round(near.lat, 2) == 22.54


def test_bare_poi_name_far_away_is_disclosed_not_planned():
    """跨城命中 ⇒ **披露**而不是直接排一份外地行程（真栈：杭州万象城 1 天行程）。"""
    import asyncio
    agent = _anchored_agent([_poi("杭州万象城", "杭州市江干区", 30.25, 120.21)])
    near, disclosure = asyncio.run(agent._city_anchor("万象城", [], _HERE))
    assert near is None
    assert disclosure is not None and disclosure.status == "need_slot"
    assert "杭州" in disclosure.speech and "公里" in disclosure.speech


def test_administrative_destination_is_never_anchored():
    """对照一：用户说的就是城市（「去三亚玩三天」）⇒ 不加锚、不披露。"""
    import asyncio
    agent = _anchored_agent([_poi("三亚湾", "三亚市", 18.25, 109.51)],
                            level=("市", "109.51,18.25"))
    assert asyncio.run(agent._city_anchor("三亚", [], _HERE)) == (None, None)


def test_multi_city_and_missing_position_are_left_alone():
    """对照二：多城行程自己点了城；拿不到当前位置就**不猜**。"""
    import asyncio
    agent = _anchored_agent([_poi("杭州万象城", "杭州市", 30.25, 120.21)])
    assert asyncio.run(
        agent._city_anchor("杭州、苏州", ["杭州", "苏州"], _HERE)) == (None, None)
    assert asyncio.run(agent._city_anchor("万象城", [], {})) == (None, None)
