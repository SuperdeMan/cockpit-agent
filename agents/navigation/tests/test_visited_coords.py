"""D4（接地卡 2026-08-14）：历史指代「上次去过的那个湖」→ episodic 轨迹坐标直取。

真栈红例 trace 966d5b16：记忆召回正确给出「滴水湖」，`_find_destination` 重搜
把最后一跳交回就近偏置接到雅悦酒店——去过的地方坐标本来就在轨迹里（批 C 写入），
名字对齐才直用、不经 LLM 转手。断言的是「记忆改变了行为」（坐标直用+零重搜），
不是「记忆被注入了」。
"""
import asyncio
import json

from agents._sdk.testing import make_context, run_handle
from agents.navigation.src.agent import NavigationAgent
from agents.navigation.src.providers.base import POI

SH = {"current_lat": "31.2317", "current_lng": "121.4692"}


def _episodic(name, lat, lng):
    return {"id": "m1", "kind": "episodic", "predicate": "", "scope": "episodic.place",
            "text": f"2026-08-14 导航去过{name}",
            "value_json": json.dumps({"name": name, "lat": lat, "lng": lng,
                                      "ts": 1755100000}, ensure_ascii=False)}


class _TrapPoi:
    """陷阱 provider：一旦被调用就返回借名酒店（真栈误伤形态），并记录调用。"""

    def __init__(self):
        self.calls = []

    async def search(self, keyword, near=None, **kw):
        self.calls.append(keyword)
        return [POI(id="trap", name="滴水湖雅悦酒店(上海老芦公路店)",
                    category="住宿服务;住宿服务相关;住宿服务相关",
                    lat=30.90, lng=121.93)]


def test_history_ref_uses_episodic_coords_zero_research():
    """「带我去上次我们去过的那个湖」+ planner 已填 dest=滴水湖 → 轨迹坐标直用，
    零 POI 重搜（重搜正是把最后一跳交回就近偏置的通道）。"""
    agent = NavigationAgent()
    poi = _TrapPoi()
    agent.poi = poi
    ctx = make_context()
    ctx._memory.recall.return_value = [_episodic("滴水湖", 30.9080, 121.9420)]

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "滴水湖"},
        raw_text="带我去上次我们去过的那个湖", ctx=ctx, meta=SH))

    assert res.status == "ok"
    nav = [a for a in res.actions if a["type"] == "navigate"]
    assert nav and abs(nav[0]["payload"]["lat"] - 30.9080) < 1e-6  # 轨迹坐标，非酒店
    assert "雅悦酒店" not in res.speech
    assert poi.calls == []  # 坐标直取，没做任何 POI 搜索


def test_history_ref_name_mismatch_falls_through():
    """名字对不齐（记忆里只有滴水湖，这次要去外滩）→ 不猜，走正常解析。"""
    agent = NavigationAgent()
    poi = _TrapPoi()
    agent.poi = poi
    ctx = make_context()
    ctx._memory.recall.return_value = [_episodic("滴水湖", 30.9080, 121.9420)]

    asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "外滩"},
        raw_text="去上次那个地方旁边的外滩", ctx=ctx, meta=SH))

    assert "外滩" in poi.calls  # 回落正常 POI 解析链


def test_no_history_ref_skips_episodic():
    """普通「导航去滴水湖」（无历史指代词）→ 不查 episodic 轨迹（episodic 噪声大，
    无差别消费会让一次到访永久劫持该目的地名）。"""
    agent = NavigationAgent()
    poi = _TrapPoi()
    agent.poi = poi
    ctx = make_context()
    ctx._memory.recall.return_value = [_episodic("滴水湖", 30.9080, 121.9420)]

    asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "滴水湖"},
        raw_text="导航去滴水湖", ctx=ctx, meta=SH))

    episodic_recalls = [
        c for c in ctx._memory.recall.await_args_list
        if (c.kwargs.get("scopes") or []) == ["episodic.place"]]
    assert episodic_recalls == []  # 没查轨迹
    assert "滴水湖" in poi.calls   # 走正常解析


def test_history_ref_recall_failure_not_blocking():
    """memory 服务失败 → 静默回落正常解析，绝不阻塞导航（best-effort 契约）。"""
    agent = NavigationAgent()
    poi = _TrapPoi()
    agent.poi = poi
    ctx = make_context()
    ctx._memory.recall.side_effect = RuntimeError("memory down")

    res = asyncio.run(run_handle(
        agent, "navigation.navigate_to", slots={"destination": "滴水湖"},
        raw_text="带我去上次去过的滴水湖", ctx=ctx, meta=SH))

    assert res.status == "ok"
    assert "滴水湖" in poi.calls  # 回落正常解析链
