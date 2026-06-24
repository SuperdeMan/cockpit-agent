"""trip-planner 契约测试（F10）。

文件名避开 test_agent.py：各 agent tests/ 无 __init__.py 时
重名模块会让根目录 pytest 收集冲突（F7 --import-mode=importlib 已缓解，
但文件名仍保持唯一以减少混淆）。

4 用例（评审决策 2026-06-11）：
1. 缺 destination → NEED_SLOT
2. happy path：mock agents.call + llm.complete → NEED_CONFIRM（Phase E 增强）
3. 协作降级：agents.call 抛异常仍返回 ok（纯 LLM 兜底）
4. manifest 一致性

Phase E 增强：trip.plan 现在返回 NEED_CONFIRM（确认方案）。
"""
import asyncio
from unittest.mock import AsyncMock

from agents._sdk.testing import run_handle, make_context, assert_manifest_consistent
from agents._sdk.result import AgentResult
from agents.trip_planner.src.agent import TripPlannerAgent


# ─── 辅助 ───

def _mock_nav_result(names: list[str]) -> AgentResult:
    """构造一个带 ui_card.items 的导航 Agent 返回。"""
    items = [{"name": n, "rating": 4.5} for n in names]
    return AgentResult(
        speech=f"找到 {len(names)} 个结果",
        ui_card={"type": "poi_list", "items": items},
    )


# ─── 用例 ───

def test_missing_destination_returns_need_slot():
    """缺 destination → NEED_SLOT，不触达 llm/agents。"""
    res = asyncio.run(run_handle(
        TripPlannerAgent(), "trip.plan", slots={}, raw_text="帮我规划行程"))
    assert res.status == "need_slot"
    assert "目的地" in res.speech or "去哪里" in res.speech


def test_happy_path_returns_trip_plan():
    """全槽位 happy path：mock agents.call + llm.complete。

    Phase E 增强：trip.plan 现在返回 NEED_CONFIRM（确认方案）。
    """
    agent = TripPlannerAgent()

    # mock agents.call：返回带 ui_card.items 的 AgentResult
    async def mock_call(agent_id, intent, slots, ctx):
        if intent == "info.weather":
            return AgentResult(speech="杭州当前晴，气温25℃")
        if intent == "info.forecast":
            return AgentResult(speech="未来3天多云转晴")
        if intent == "charging.plan":
            return AgentResult(speech="无需中途充电")
        return _mock_nav_result(["西湖", "灵隐寺", "千岛湖"])

    agent._agents = type("MockAgents", (), {"call": mock_call})()
    agent.llm.complete = AsyncMock(return_value="第一天：西湖漫步，第二天：灵隐寺祈福。")

    res = asyncio.run(run_handle(
        agent, "trip.plan",
        slots={"destination": "杭州", "days": "2天"},
        raw_text="杭州两天自驾游"))
    # Phase E：trip.plan 返回 NEED_CONFIRM（确认方案）
    assert res.status == "need_confirm"
    assert "第一天" in res.speech or "杭州" in res.speech


def test_agents_call_failure_falls_back_to_llm():
    """协作降级：agents.call 抛异常，仍返回 NEED_CONFIRM（纯 LLM 兜底不向上抛）。"""
    agent = TripPlannerAgent()

    async def mock_call_fail(*args, **kwargs):
        raise RuntimeError("navigation agent unreachable")

    agent._agents = type("MockAgents", (), {"call": mock_call_fail})()
    agent.llm.complete = AsyncMock(return_value="第一天：自由活动，第二天：返程。")

    res = asyncio.run(run_handle(
        agent, "trip.plan",
        slots={"destination": "三亚", "days": "3天"},
        raw_text="三亚三天"))
    # Phase E：trip.plan 返回 NEED_CONFIRM
    assert res.status == "need_confirm"
    assert "第一天" in res.speech


def test_confirmed_resume_finalizes_without_replanning():
    """确认轮（meta.confirmed=true）应收尾返回 OK，绝不重跑规划/再返回 NEED_CONFIRM。

    回归：确认按钮曾导致"确认→重规划→再确认"死循环（trip.plan 未判 confirmed）。
    确认路径不得触达 agents.call / llm（直接收尾）。"""
    agent = TripPlannerAgent()

    async def must_not_call(*args, **kwargs):
        raise AssertionError("确认轮不应再调用其他 Agent 重规划")

    agent._agents = type("MockAgents", (), {"call": must_not_call})()
    agent.llm.complete = AsyncMock(side_effect=AssertionError("确认轮不应再调 LLM"))

    res = asyncio.run(run_handle(
        agent, "trip.plan",
        slots={"destination": "杭州", "days": "2"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert res.status != "need_confirm"
    assert "杭州" in res.speech and "已确认" in res.speech


def test_plan_confirmed_offers_first_stop_pois():
    """确认轮：把第一站候选景点作 plain poi_list 让用户选『第几个』即导航。

    回归 issue 3：确认方案后目的地设为第一站，且第一站可搜索 POI 供选择。"""
    agent = TripPlannerAgent()
    # 模拟上一轮 _plan 已缓存行程上下文（含 Day1 候选景点）
    agent._sessions["test-sess"] = {
        "destination": "北京", "days": "3", "itinerary": "第一天：颐和园…",
        "pois": [{"name": "颐和园", "rating": 4.7}, {"name": "故宫", "rating": 4.8},
                 {"name": "天坛", "rating": 4.6}],
    }
    agent.llm.complete = AsyncMock(side_effect=AssertionError("确认轮不应再调 LLM"))

    res = asyncio.run(run_handle(
        agent, "trip.plan", slots={"destination": "北京", "days": "3"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert res.ui_card and res.ui_card.get("type") == "poi_list"
    assert res.ui_card.get("purpose") is None  # plain poi_list → HMI「第N个」即导航
    names = [i["name"] for i in res.ui_card["items"]]
    assert "颐和园" in names
    assert "第几个" in res.speech


def test_confirmed_first_stop_searches_day1_place():
    """确认后第一站 = 行程第一天的景点（天坛公园），实时搜 POI 供选择。

    回归 issue 2：确认前没确认第一天目的地 POI、目的地也没用行程里第一天的天坛公园。"""
    agent = TripPlannerAgent()
    agent._sessions["test-sess"] = {
        "destination": "北京", "days": "3",
        "itinerary": "第一天：天坛公园…第二天：故宫", "pois": [],
        "first_stop": "天坛公园",
    }

    calls = {}

    async def mock_call(self, agent_id, intent, slots, ctx):   # self: 绑定方法
        calls["kw"] = slots.get("keyword")
        return AgentResult(speech="找到", ui_card={"type": "poi_list", "items": [
            {"name": "天坛公园-东门", "address": "东城区"},
            {"name": "天坛公园-南门", "address": "东城区"}]})

    agent._agents = type("MockAgents", (), {"call": mock_call})()
    agent.llm.complete = AsyncMock(side_effect=AssertionError("确认轮不应再调 LLM"))

    res = asyncio.run(run_handle(
        agent, "trip.plan", slots={"destination": "北京", "days": "3"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert calls["kw"] == "天坛公园"                # 按第一天景点搜，而非泛搜
    assert res.ui_card["type"] == "poi_list"
    names = [i["name"] for i in res.ui_card["items"]]
    assert "天坛公园-东门" in names
    assert "天坛公园" in res.speech


def test_first_stop_extraction_from_itinerary():
    """从行程文本解析第一天主要景点（去掉时间/动词前缀）。"""
    from agents.trip_planner.src.agent import _first_stop_from_itinerary
    assert _first_stop_from_itinerary("第一天：上午抵达后，下午可前往天坛公园。第二天：故宫") == "天坛公园"
    assert _first_stop_from_itinerary("第一天：八达岭长城（缆车）。第二天") == "八达岭长城"
    assert _first_stop_from_itinerary("没有分天的纯文本") == ""


def test_modify_preserves_prior_itinerary():
    """改第N天：必须带上原行程，只改提到的天。回归 issue 1（曾生成占位【XX城市】）。"""
    agent = TripPlannerAgent()
    prior = ("第一天：颐和园长廊昆明湖；第二天：故宫天坛；"
             "第三天：奥林匹克公园+烤鸭")
    agent._sessions["test-sess"] = {
        "destination": "北京", "days": "3", "itinerary": prior, "pois": []}
    captured = {}

    async def fake_complete(messages, **kw):
        captured["user"] = messages[-1]["content"]
        return "第一天：颐和园；第二天：故宫天坛；第三天：改为798艺术区"

    agent.llm.complete = fake_complete
    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第三天换成798"},
        raw_text="第三天换成798"))
    assert res.status == "need_confirm"
    # 原行程作为上下文喂给了 LLM（前两天得以保留）
    assert prior in captured["user"]
    assert "颐和园" in captured["user"]
    # 缓存里的行程已更新
    assert "798" in agent._sessions["test-sess"]["itinerary"]


def test_modify_confirmed_resume_finalizes():
    """改行程确认轮同样收尾，不再 NEED_CONFIRM。回归 issue 2（modify 确认死循环）。"""
    agent = TripPlannerAgent()
    agent._sessions["test-sess"] = {
        "destination": "北京", "days": "3", "itinerary": "…", "pois": []}
    agent.llm.complete = AsyncMock(side_effect=AssertionError("确认轮不应再调 LLM"))

    res = asyncio.run(run_handle(
        agent, "trip.modify", slots={"modification": "第三天换成798"},
        raw_text="确认", meta={"confirmed": "true"}))
    assert res.status == "ok"
    assert "已确认" in res.speech


def test_manifest_consistency():
    """manifest 一致性校验。"""
    assert assert_manifest_consistent(TripPlannerAgent()) is True
