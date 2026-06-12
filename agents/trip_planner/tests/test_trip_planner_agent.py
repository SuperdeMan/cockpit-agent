"""trip-planner 契约测试（F10）。

文件名避开 test_agent.py：各 agent tests/ 无 __init__.py 时
重名模块会让根目录 pytest 收集冲突（F7 --import-mode=importlib 已缓解，
但文件名仍保持唯一以减少混淆）。

4 用例（评审决策 2026-06-11）：
1. 缺 destination → NEED_SLOT
2. happy path：mock agents.call + llm.complete → ok + ui_card
3. 协作降级：agents.call 抛异常仍返回 ok（纯 LLM 兜底）
4. manifest 一致性
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
        TripPlannerAgent(), "trip.plan_trip", slots={}, raw_text="帮我规划行程"))
    assert res.status == "need_slot"
    assert "目的地" in res.speech or "去哪里" in res.speech


def test_happy_path_returns_trip_plan():
    """全槽位 happy path：mock agents.call + llm.complete。"""
    agent = TripPlannerAgent()

    # mock agents.call：返回带 ui_card.items 的 AgentResult
    async def mock_call(agent_id, intent, slots, ctx):
        return _mock_nav_result(["西湖", "灵隐寺", "千岛湖"])

    agent._agents = type("MockAgents", (), {"call": mock_call})()
    agent.llm.complete = AsyncMock(return_value="第一天：西湖漫步，第二天：灵隐寺祈福。")

    res = asyncio.run(run_handle(
        agent, "trip.plan_trip",
        slots={"destination": "杭州", "days": "2天"},
        raw_text="杭州两天自驾游"))
    assert res.status == "ok"
    assert res.ui_card["type"] == "trip_plan"
    assert res.ui_card["destination"] == "杭州"
    assert "西湖" in res.ui_card.get("pois", "") or "第一天" in res.speech


def test_agents_call_failure_falls_back_to_llm():
    """协作降级：agents.call 抛异常，仍返回 ok（纯 LLM 兜底不向上抛）。"""
    agent = TripPlannerAgent()

    async def mock_call_fail(*args, **kwargs):
        raise RuntimeError("navigation agent unreachable")

    agent._agents = type("MockAgents", (), {"call": mock_call_fail})()
    agent.llm.complete = AsyncMock(return_value="第一天：自由活动，第二天：返程。")

    res = asyncio.run(run_handle(
        agent, "trip.plan_trip",
        slots={"destination": "三亚", "days": "3天"},
        raw_text="三亚三天"))
    assert res.status == "ok"
    assert "第一天" in res.speech


def test_manifest_consistency():
    """manifest 一致性校验。"""
    assert assert_manifest_consistent(TripPlannerAgent()) is True
