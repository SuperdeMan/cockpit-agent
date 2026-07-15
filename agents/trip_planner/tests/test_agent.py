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
