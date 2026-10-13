"""位置提醒解析 + 围栏判定（M3 P1）契约测试。"""
from __future__ import annotations

import pytest

from agents.reminder.src.geofence import GeofenceWatcher
from agents.reminder.src.placeparse import (ARRIVE, LEAVE, arrived, haversine_m,
                                            name_hit, parse_place_text)
from agents.reminder.src.store import LOCATION, PENDING, Reminder, ReminderStore


# ── 解析 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,place,title", [
    ("到公司提醒我拿文件", "公司", "拿文件"),
    ("到家提醒我收快递", "家", "收快递"),
    ("到了机场提醒我打车", "机场", "打车"),
    ("到公司的时候提醒我交周报", "公司", "交周报"),
    ("回到家就提醒我吃药", "家", "吃药"),
])
def test_arrive_forms(text, place, title):
    p = parse_place_text(text)
    assert p.ok and p.place == place and p.trigger_on == ARRIVE
    assert p.title == title


def test_leave_form():
    p = parse_place_text("离开公司提醒我买菜")
    assert p.ok and p.place == "公司" and p.trigger_on == LEAVE and p.title == "买菜"


@pytest.mark.parametrize("text", [
    # ETA 族：已由 P1c REMINDABLE_ACTIVE 交接链走通（旅程 B5-1 绿），本模块必须让路，
    # 两条链路抢同一句话是 badcase 制造机
    "到公司之前提醒我交周报",
    "快到的时候提醒我给张姐打电话",
    "到达前一刻钟提醒我",
    "到之前提醒我",
])
def test_eta_family_yields(text):
    assert parse_place_text(text).ok is False


@pytest.mark.parametrize("text", [
    "明天早上八点提醒我带充电线",
    "提醒我吃降压药",
    "半小时后叫我",
    "",
    "到时候提醒我",          # 「时候」不是地点
])
def test_non_location_sentences_untouched(text):
    assert parse_place_text(text).ok is False


@pytest.mark.parametrize("text", [
    "到我老婆生日那天早上八点提醒我订花",   # 核心旅程 E07（`284b52ce`）：建成了地点「我老婆生日那天早上八点」的到达提醒
    "到我老婆生日那天提醒我订花",
    "到八点提醒我吃药",
    "到明天早上提醒我交周报",
    "到下午三点提醒我开会",
    "到周一提醒我交周报",
    "到5号提醒我交房租",
    "到五月二十号提醒我订花",
])
def test_arriving_at_a_time_is_not_an_arrival_reminder(text):
    """「到 + 时间 + 提醒我」到的是一个时间：此前全被建成到达提醒，永远不会触发，话术却答应了。"""
    assert parse_place_text(text).ok is False


@pytest.mark.parametrize("text,place", [
    ("到3号门提醒我拿快递", "3号门"),
    ("到三号楼提醒我拿文件", "三号楼"),
    ("到中山路88号提醒我取件", "中山路88号"),   # 门牌：「N号」不是整截日期
    ("到明天广场提醒我买票", "明天广场"),
    ("到8号线提醒我换乘", "8号线"),
])
def test_places_with_numbers_or_day_words_inside_are_still_places(text, place):
    """只看收尾：带数字、带日词但以地名收尾的照旧是地点。"""
    p = parse_place_text(text)
    assert p.ok and p.place == place and p.trigger_on == ARRIVE


# ── 距离与匹配 ──────────────────────────────────────────────────────────

def test_haversine_known_distance():
    # 天安门 → 故宫，约 900m 量级
    d = haversine_m(39.9087, 116.3975, 39.9163, 116.3972)
    assert 700 < d < 1000


def test_haversine_missing_coord_is_inf():
    assert haversine_m(None, 116.4, 39.9, 116.4) == float("inf")


def test_name_hit_is_bidirectional_containment():
    assert name_hit("公司", {"name": "XX公司总部"})
    assert name_hit("北京西站", {"name": "北京西站"})
    assert not name_hit("公司", {"name": "望京SOHO"})
    assert not name_hit("公司", None)


def test_arrived_prefers_coordinates_over_name():
    extra = {"place": "公司", "lat": 39.9087, "lon": 116.3975, "radius_m": 300}
    assert arrived(extra, {"lat": 39.9090, "lng": 116.3978, "name": "无关地名"})
    assert not arrived(extra, {"lat": 39.99, "lng": 116.50, "name": "公司"})


def test_arrived_falls_back_to_name_without_coords():
    assert arrived({"place": "机场"}, {"name": "首都国际机场T3"})
    assert not arrived({"place": "机场"}, {"name": "望京SOHO"})


# ── 围栏边沿 ────────────────────────────────────────────────────────────

class Cap:
    def __init__(self):
        self.sent = []

    async def publish(self, p):
        self.sent.append(p)


async def _store_with(reminder) -> ReminderStore:
    st = ReminderStore(dsn="")
    await st.init()
    await st.add(reminder)
    return st


def loc_reminder(**extra):
    e = {"place": "公司", "trigger_on": ARRIVE,
         "lat": 39.9087, "lon": 116.3975, "radius_m": 300}
    e.update(extra)
    return Reminder(user_id="u1", title="拿文件", kind=LOCATION, status=PENDING, extra=e)


INSIDE = {"lat": 39.9089, "lng": 116.3977}
OUTSIDE = {"lat": 39.99, "lng": 116.50}


@pytest.mark.asyncio
async def test_first_observation_only_seeds_never_fires():
    """人已经在公司时创建提醒，首次观测**不该**立刻响——用户要的是「下次到」。"""
    cap = Cap()
    st = await _store_with(loc_reminder())
    w = GeofenceWatcher(st, cap.publish, now_fn=lambda: 1000.0)
    assert await w.on_state([], {"location": INSIDE}) == 0
    assert cap.sent == []


@pytest.mark.asyncio
async def test_arrive_edge_fires_once():
    cap = Cap()
    st = await _store_with(loc_reminder())
    w = GeofenceWatcher(st, cap.publish, now_fn=lambda: 1000.0)
    await w.on_state([], {"location": OUTSIDE})            # 播种：在外面
    assert await w.on_state([], {"location": INSIDE}) == 1  # 进围栏 → 触发
    assert await w.on_state([], {"location": INSIDE}) == 0  # 持续在里面不重复
    assert len(cap.sent) == 1
    p = cap.sent[0]
    assert "到公司了" in p["speech"] and "拿文件" in p["speech"]
    assert p["priority"] == "user_contract"                # 到地必响，同到点必响
    assert p["card"]["item"]["time_display"] == "到公司时"


@pytest.mark.asyncio
async def test_leave_edge_requires_having_been_inside():
    cap = Cap()
    st = await _store_with(loc_reminder(trigger_on=LEAVE))
    w = GeofenceWatcher(st, cap.publish, now_fn=lambda: 1000.0)
    await w.on_state([], {"location": OUTSIDE})            # 播种：一直在外面
    assert await w.on_state([], {"location": OUTSIDE}) == 0  # 没进去过就谈不上离开
    await w.on_state([], {"location": INSIDE})
    assert await w.on_state([], {"location": OUTSIDE}) == 1


@pytest.mark.asyncio
async def test_claim_is_atomic_so_fired_item_never_double_publishes():
    cap = Cap()
    st = await _store_with(loc_reminder())
    w = GeofenceWatcher(st, cap.publish, now_fn=lambda: 1000.0)
    await w.on_state([], {"location": OUTSIDE})
    await w.on_state([], {"location": INSIDE})
    # 条目已 fired → 不再出现在 pending 列表，边沿状态也随之清掉
    assert await st.list_location_pending() == []
    await w.on_state([], {"location": OUTSIDE})
    assert await w.on_state([], {"location": INSIDE}) == 0
    assert len(cap.sent) == 1


@pytest.mark.asyncio
async def test_no_location_in_state_is_noop():
    cap = Cap()
    st = await _store_with(loc_reminder())
    w = GeofenceWatcher(st, cap.publish, now_fn=lambda: 1000.0)
    assert await w.on_state([], {"speed_kmh": 30}) == 0


# ── Agent 级：创建位置提醒 ───────────────────────────────────────────────

async def _agent(resolve=None):
    from unittest.mock import AsyncMock

    from agents.reminder.src.agent import ReminderAgent
    from agents.reminder.src.timeparse import FAIL, ParsedTime
    a = ReminderAgent()
    a.store = ReminderStore(dsn="")
    await a.store.init()
    a._llm_time_fallback = AsyncMock(return_value=ParsedTime(FAIL))
    a._resolve_place = AsyncMock(return_value=resolve)
    return a


@pytest.mark.asyncio
async def test_agent_creates_location_reminder():
    from agents._sdk.testing import run_handle
    a = await _agent(resolve={"lat": 39.9, "lon": 116.4, "radius_m": 300})
    res = await run_handle(a, "reminder.create", raw_text="到公司提醒我拿文件")
    assert res.status == "ok"
    assert "到公司我就提醒你" in res.speech and "拿文件" in res.speech
    pend = await a.store.list_location_pending()
    assert len(pend) == 1 and pend[0].extra["place"] == "公司"
    assert pend[0].extra["trigger_on"] == ARRIVE and pend[0].kind == LOCATION


@pytest.mark.asyncio
async def test_agent_refuses_honestly_when_place_unresolvable():
    """**绝不存一条永远不会触发的提醒**——解析不出地点就问，不假装记下了。"""
    from agents._sdk.testing import run_handle
    a = await _agent(resolve=None)
    res = await run_handle(a, "reminder.create", raw_text="到某个不存在的地方提醒我拿文件")
    assert res.status == "need_slot" and "place_address" in res.missing_slots
    assert await a.store.list_location_pending() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("time_text", ["", "我老婆生日那天早上八点", "老婆生日当天早上八点"])
async def test_a_day_only_the_user_knows_is_asked_not_an_arrival_or_tomorrow(time_text):
    """核心旅程 E07（`284b52ce`）：「到我老婆生日那天早上八点提醒我订花」被建成到达提醒——地点那截经周边搜索落到了随便一个
    POI 上（这里用能解析出坐标的桩模拟），卡片写「到我老婆生日那天早上八点时」，话术答应了「到那天早上八点提醒你」。
    地点那层放过之后，时间解析又会丢掉认不出的那一天、排成明天 08:00。两层都不许：什么都不建，诚实问什么时候。"""
    from agents._sdk.testing import run_handle
    a = await _agent(resolve={"lat": 22.54, "lon": 113.94, "radius_m": 300})
    res = await run_handle(a, "reminder.create", raw_text="到我老婆生日那天早上八点提醒我订花",
                           slots={"title": "订花", "time_text": time_text})
    assert res.status == "need_slot" and "什么时候" in res.speech
    assert await a.store.list_location_pending() == []
    times, _ = await a.store.list_split("u1")
    assert times == []


@pytest.mark.asyncio
async def test_the_same_sentence_with_the_date_resolved_is_a_timed_reminder():
    """规划器从记忆里把那一天解析成了日期（`284b52ce` 第 1 遍的「五月二十号早上八点」）：照日期建定时提醒，不再被到达句式截走。"""
    from agents._sdk.testing import run_handle
    a = await _agent(resolve={"lat": 22.54, "lon": 113.94, "radius_m": 300})
    res = await run_handle(a, "reminder.create", raw_text="到我老婆生日那天早上八点提醒我订花",
                           slots={"title": "订花", "time_text": "五月二十号早上八点"})
    assert res.status == "ok" and "5月20日" in res.speech and "08:00" in res.speech
    assert await a.store.list_location_pending() == []
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].kind == "time" and times[0].title == "订花"


@pytest.mark.asyncio
async def test_the_time_fallback_does_not_ask_the_model_to_guess_a_day_only_the_user_knows():
    """「我老婆生日那天」是哪天，模型和规则一样不知道：时间兜底不调模型，直接失败交外层追问；别的规则未命中照旧问模型。"""
    from unittest.mock import AsyncMock

    from agents.reminder.src.agent import ReminderAgent
    from agents.reminder.src.timeparse import FAIL, OK
    a = ReminderAgent()
    a.llm.complete = AsyncMock(return_value='{"iso": "2027-01-06T12:00"}')
    assert (await a._llm_time_fallback("老婆生日当天早上八点")).status == FAIL
    a.llm.complete.assert_not_called()
    assert (await a._llm_time_fallback("下下周三饭点")).status == OK
    a.llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_time_reminders_are_untouched_by_location_branch():
    """零回归护栏：位置分支挪到了时间解析之前，普通定时提醒必须逐字照旧。"""
    from agents._sdk.testing import run_handle
    a = await _agent(resolve={"lat": 39.9, "lon": 116.4})
    res = await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    assert res.status == "ok" and "08:00" in res.speech
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].kind == "time"
