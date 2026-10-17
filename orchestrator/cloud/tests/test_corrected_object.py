"""改口只换了对象的确定性消费（核心旅程 K08，2026-10-17）。

「打开油箱盖」挂着确认，用户说「不对，是充电口」：动作沿用被否掉的提议（open），对象换成这句点名的那个——与 Q7 省略式开关
（对象来自执行账本、动作来自原话）对称。交给规划器有方差（`9aa84404` 5 遍 2 遍错：一遍 `charging_port.close`、一遍两轮零步落闲聊）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog


def _vehicle(*caps, agent_id="edge-vehicle"):
    agent = MagicMock()
    agent.manifest.agent_id = agent_id
    agent.manifest.capabilities = []
    agent.manifest.latency_budget_ms = 2000
    agent.manifest.kind = "edge_fast"
    agent.manifest.deployment = "edge"
    agent.manifest.requires_permissions = []
    agent.manifest.trust_level = "first_party"
    agent.manifest.context_scopes = []
    for intent, description in caps:
        cap = MagicMock()
        cap.intent, cap.description, cap.slots, cap.examples = intent, description, [], []
        cap.heavy = cap.whole_utterance = cap.response_only = False
        cap.require_confirm = True
        cap.effect = ""
        agent.manifest.capabilities.append(cap)
    agent.endpoint = f"stub:{agent_id}"
    return agent


_CAPS = [("fuel_tank_cover.open", "打开油箱盖"), ("fuel_tank_cover.close", "关闭油箱盖"),
         ("charging_port.open", "打开充电口"), ("charging_port.close", "关闭充电口"),
         ("trunk.open", "打开后备箱"), ("charging.start", "开始充电")]


def _plan(text, rejected="fuel_tank_cover.open", caps=_CAPS):
    catalog = _assemble_capability_catalog([_vehicle(*caps)])
    return PlanBuilder._corrected_object_plan(text, rejected, catalog)


def test_the_object_changes_and_the_action_of_the_rejected_proposal_is_kept():
    for text in ("不对，是充电口", "不对不对，是充电口", "不是油箱盖，是充电口", "说错了，我说的是充电口", "不对，是充电口才对",
                 "不对，是打开充电口"):        # 同方向的动作词照接（反方向的见下一条）
        plan = _plan(text)
        assert plan is not None and [s.intent for s in plan.steps] == ["charging_port.open"], text
        assert plan.steps[0].require_confirm, "新提议照样挂确认"
    assert [s.intent for s in _plan("不对，是充电口", rejected="fuel_tank_cover.close").steps] == ["charging_port.close"]


def test_anything_beyond_a_bare_object_goes_back_to_the_planner():
    """说了反方向的动作（对象和动作都换了）、没否掉提议、对象点名不出、提议没有动作段：一律交回正常规划。"""
    assert _plan("不对，是关充电口") is None                # 点名判据：说反了方向不算点名
    assert _plan("不对，是关闭充电口") is None
    assert _plan("是充电口") is None                       # 没有否掉提议的开头
    assert _plan("不对，帮我看看附近有什么景点") is None
    assert _plan("不对，是天窗") is None                    # 目录里没有点得到它的同动作能力
    assert _plan("不对，是充电口", rejected="") is None
    assert _plan("不对，是充电口", rejected="navigation") is None


def test_an_ambiguous_object_is_not_guessed():
    caps = [*_CAPS, ("charging_port_cover.open", "打开充电口")]
    assert _plan("不对，是充电口", caps=caps) is None


# ── 引擎全链路：挂着油箱盖的确认，说「不对，是充电口」──────────────────────────────────────────

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.session import SessionStore


class _Cap:
    def __init__(self, intent, description):
        self.intent, self.description, self.slots, self.examples = intent, description, [], []
        self.heavy = self.whole_utterance = self.response_only = False
        self.require_confirm = True
        self.effect = ""


def _cloud_vehicle():
    manifest = SimpleNamespace(
        agent_id="vehicle", trust_level="first_party", latency_budget_ms=2000, deployment="cloud",
        kind="agent", requires_permissions=[], context_scopes=[], route_hints=[],
        capabilities=[_Cap(intent, description) for intent, description in _CAPS])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50090")


class _Resp:
    def __init__(self, status=0, speech=""):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _Spy:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.llm_plan_calls = 0

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        name = dict(_CAPS)[intent]
        if (meta or {}).get("confirmed") == "true":
            return _Resp(speech=f"已{name}。")
        return _Resp(status=1, speech=f"确认{name}吗？")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            self.llm_plan_calls += 1
            ref = _assemble_capability_catalog([_cloud_vehicle()]).pair_to_ref[("vehicle", "fuel_tank_cover.open")]
            return json.dumps({"steps": [{"id": "s1", "capability_ref": ref, "slots": {},
                                          "depends_on": [], "slot_refs": {}}]})
        return "（聚合话术）"

    async def resolve(self, query="", intent="", top_k=1):
        return [_cloud_vehicle()]

    async def list_agents(self):
        return [_cloud_vehicle()]


def _turn(engine, text, *, confirm=False):
    req = SimpleNamespace(text=text, session_id="sess-k08", request_id="r1", is_confirmation=confirm,
                          operation_id="", context=SimpleNamespace(user_id="u1", vehicle_id="v1"))

    async def collect():
        return [event async for event in engine.run(req)]
    return asyncio.run(collect())


def test_a_correction_moves_the_pending_confirmation_to_the_named_object():
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=session)

    first = _turn(engine, "打开油箱盖")[-1]
    assert first.get("need_confirm") and spy.calls[-1][0] == "fuel_tank_cover.open"

    second = _turn(engine, "不对，是充电口")[-1]
    assert spy.llm_plan_calls == 1, "改口由确定性消费方接住，不再问规划模型"
    assert spy.calls[-1][0] == "charging_port.open" and spy.calls[-1][1].get("confirmed") != "true"
    assert second.get("need_confirm") and "充电口" in second.get("speech", "")
    state = asyncio.run(session.load("sess-k08", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_confirm"

    _turn(engine, "确认", confirm=True)
    executed = [intent for intent, meta in spy.calls if meta.get("confirmed") == "true"]
    assert executed == ["charging_port.open"], "被否掉的油箱盖从头到尾没执行"
