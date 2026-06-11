"""PlannerEngine 多轮确认闭环测试（F1 回归）。

覆盖：确认完成下单且不重跑已完成步骤、取消、确认标记无挂起任务、
语音短肯定话术兜底、答非所问按新请求处理。
全部用进程内 stub，不依赖 gRPC/proto 生成代码。
"""
from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.session import SessionStore
from security.permission import PermissionEngine

_PLAN_JSON = json.dumps({
    "steps": [
        {"id": "s1", "agent_id": "food-ordering", "intent": "food.search_restaurant",
         "slots": {"cuisine": "川菜"}, "depends_on": []},
        {"id": "s2", "agent_id": "food-ordering", "intent": "food.reserve",
         "slots": {"restaurant_name": "川菜·名店1", "datetime": "今晚7点", "party_size": "2"},
         "depends_on": ["s1"]},
    ]
})

_AGG_SPEECH = "好的，已为您找到川菜·名店1并订好今晚7点两位。"


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description = intent, slots, intent


def _food_agent():
    manifest = SimpleNamespace(
        agent_id="food-ordering",
        trust_level="third_party",
        latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[
            _Cap("food.search_restaurant", ["cuisine"]),
            _Cap("food.reserve", ["restaurant_name", "datetime", "party_size"]),
        ],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50063")


class _Resp:
    def __init__(self, status=0, speech="", follow_up=""):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = []
        self.ui_card = None


class _Spy:
    """记录每次 agent 调用的 (intent, meta)，并按脚本返回结果。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.llm_plan_calls = 0

    def count(self, intent: str) -> int:
        return sum(1 for i, _ in self.calls if i == intent)

    def metas(self, intent: str) -> list[dict]:
        return [m for i, m in self.calls if i == intent]

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        if intent == "food.search_restaurant":
            return _Resp(speech="为您找到 3 家川菜。")
        if intent == "food.reserve":
            if (meta or {}).get("confirmed") == "true":
                return _Resp(speech="已为您订好：川菜·名店1 今晚7点 2位。")
            return _Resp(status=1, speech="确认为您预订川菜·名店1 今晚7点 2位吗？",
                         follow_up="说『确认』即可下单")
        return _Resp(status=3, speech="未知意图")

    async def llm(self, messages):
        system = messages[0]["content"]
        if "任务编排器" in system:
            self.llm_plan_calls += 1
            return _PLAN_JSON
        return _AGG_SPEECH

    async def resolve(self, query="", intent="", top_k=1):
        return [_food_agent()]

    async def list_agents(self):
        return [_food_agent()]


def _make_engine() -> tuple[PlannerEngine, _Spy, SessionStore]:
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
        perms=PermissionEngine(),
    )
    return engine, spy, session


def _req(text: str, session_id: str = "sess-1", is_confirmation: bool = False):
    return SimpleNamespace(
        text=text, session_id=session_id, request_id="r1",
        is_confirmation=is_confirmation,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req) -> list[dict]:
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


# ─── 闭环主路径 ───

def test_confirm_completes_reservation_without_rerunning_done_steps():
    engine, spy, session = _make_engine()

    # 第 1 轮：搜索 OK → 预订挂起 NEED_CONFIRM
    events = _run(engine, _req("找家川菜馆订今晚7点两位"))
    final = events[-1]
    assert final["need_confirm"] is True
    assert spy.count("food.search_restaurant") == 1
    assert spy.count("food.reserve") == 1
    state = asyncio.run(session.load("sess-1"))
    assert state is not None and state.phase == "wait_confirm"
    assert state.pending_step_id == "s2"

    # 第 2 轮：HMI 确认按钮（is_confirmation=true）
    events = _run(engine, _req("确认", is_confirmation=True))
    final = events[-1]

    # 已完成的搜索步骤不重跑；挂起步骤带 confirmed 重跑并完成
    assert spy.count("food.search_restaurant") == 1
    assert spy.count("food.reserve") == 2
    assert spy.metas("food.reserve")[-1].get("confirmed") == "true"
    assert not final.get("need_confirm")
    assert final["speech"] == _AGG_SPEECH
    # 会话清理，确认不可重放
    assert asyncio.run(session.load("sess-1")) is None


def test_cancel_clears_pending_and_does_not_execute():
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    events = _run(engine, _req("取消", is_confirmation=True))
    final = events[-1]
    assert "取消" in final["speech"]
    assert spy.count("food.reserve") == 1          # 没有再执行
    assert asyncio.run(session.load("sess-1")) is None


def test_confirm_flag_without_pending_session():
    engine, spy, _ = _make_engine()
    events = _run(engine, _req("确认", is_confirmation=True))
    assert "没有待确认" in events[-1]["speech"]
    assert spy.llm_plan_calls == 0                  # 不会拿"确认"二字去规划


def test_voice_short_yes_resumes_without_flag():
    """语音说"订吧"（无 is_confirmation 标记）也应续接挂起任务。"""
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    events = _run(engine, _req("订吧"))
    final = events[-1]
    assert spy.metas("food.reserve")[-1].get("confirmed") == "true"
    assert not final.get("need_confirm")
    assert asyncio.run(session.load("sess-1")) is None


def test_unrelated_reply_treated_as_new_request():
    """挂起期间换话题：丢弃挂起任务，按新请求重新规划。"""
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    assert spy.llm_plan_calls == 1

    events = _run(engine, _req("附近有什么好玩的景点推荐一下"))
    final = events[-1]
    assert spy.llm_plan_calls == 2                  # 走了新规划
    assert "取消" not in final["speech"] and "过期" not in final["speech"]
    # 新规划重跑了搜索（确认续接则不会）
    assert spy.count("food.search_restaurant") == 2


# ─── 确认话术判定 ───

def test_confirm_reply_rules():
    f = PlannerEngine._confirm_reply
    assert f("取消", True) == "no"                  # 否定优先于显式标记
    assert f("确认", True) == "yes"
    assert f("订吧", False) == "yes"                # 语音短肯定
    assert f("好的", False) == "yes"
    assert f("帮我看看附近有什么充电站好吗", False) is None   # 长句不误判
    assert f("", False) is None
