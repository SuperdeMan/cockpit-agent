"""C5-B：一个补槽问题不许劫持整轮（2026-08-28，QA P1-03/P1-04）。

真栈原形 T50「接孩子放学，顺便找麦当劳，5点到校」：nearby 步丢了「麦当劳」槽 →
NEED_SLOT 反问 → **同轮的 navigate 一次都没发出去**。复合句被一个补槽问题整体劫持。

这里验的是编排层那一半（执行器那一半在 `test_executor.py`）：
① 兄弟步的结果进 `prior`，简报要说出来；② 兄弟步的**动作**要真的发出去
——话说了事没做比不说更糟。全部进程内 stub，不依赖 gRPC/proto。
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

_PLAN_JSON = json.dumps({
    "steps": [
        {"id": "s1", "capability_ref": "cap_0001", "slots": {},
         "depends_on": [], "slot_refs": {}},
        {"id": "s2", "capability_ref": "cap_0002",
         "slots": {"destination": "学校"}, "depends_on": [], "slot_refs": {}},
    ]
})


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description = intent, slots, intent


def _agents():
    nearby = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="nearby", trust_level="third_party", latency_budget_ms=2000,
        requires_permissions=[], capabilities=[_Cap("nearby.search", ["keyword"])],
    ), endpoint="stub:1")
    nav = SimpleNamespace(manifest=SimpleNamespace(
        agent_id="navigation", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap("navigation.navigate_to", ["destination"])],
    ), endpoint="stub:2")
    return [nearby, nav]


class _Act:
    def __init__(self, type_, payload=None):
        self.type = type_
        self.payload = payload or {}
        self.require_confirm = False


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", actions=None,
                 missing_slots=None):
        self.status = status
        self.speech = speech
        self.follow_up = follow_up
        self.actions = actions or []
        self.ui_card = None
        self.data = None
        self.missing_slots = missing_slots or []


class _Spy:
    def __init__(self):
        self.calls: list[str] = []

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append(intent)
        if intent == "nearby.search":
            return _Resp(status=2, speech="您想找哪家店？",
                         follow_up="说个店名就行", missing_slots=["keyword"])
        return _Resp(speech="已为您导航到学校。",
                     actions=[_Act("navigate", {"destination": "学校"})])

    async def llm(self, messages, **kwargs):
        system = messages[0]["content"]
        return _PLAN_JSON if "任务编排器" in system else "聚合话术"

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def list_agents(self):
        return _agents()


def _run(text: str):
    spy = _Spy()
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=SessionStore(redis_url=""),
    )
    req = SimpleNamespace(
        text=text, session_id="sess-c5b", request_id="r1",
        is_confirmation=False, operation_id="",
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))

    async def collect():
        return [e async for e in engine.run(req)]

    return asyncio.run(collect()), spy


def test_a_need_slot_step_does_not_swallow_the_sibling_navigation():
    events, spy = _run("接孩子放学，顺便找家店")
    final = events[-1]
    # 两步都真的执行了
    assert set(spy.calls) == {"nearby.search", "navigation.navigate_to"}
    # 挂起仍然成立（要问哪家店）
    assert final["kind"] == "final"
    assert "您想找哪家店" in final["speech"]
    # ① 兄弟步的话术进了挂起前缀——否则用户会被凭空追问
    assert "已为您导航到学校" in final["speech"]
    # ② 兄弟步的**动作**真的发出去了（此前挂起 final 只带挂起那一步的 actions）
    assert [a["type"] for a in final["actions"]] == ["navigate"]


def test_the_suspension_is_still_recorded_for_the_next_turn():
    """兄弟步照跑**不影响**挂起本身：会话态还得等着那个槽。"""
    events, _spy = _run("接孩子放学，顺便找家店")
    assert events[-1].get("operation_id")
    assert events[-1].get("need_confirm") is False
