"""需确认的车端命令：写步方向以车端的确定性解析为准（评审四轮 §5.5 b，2026-09-25）。

真栈 `5ca289c7` RS34：新会话里「关闭后备箱」被规划成 `trunk.open`（编号相邻差一位），一句「好的」开了后备箱；车端早就解出了 `trunk.close`，
在「需确认、整句上云」那条路上盖章成 `_edge_confirm`。口径只剩一种形态：计划里**同一对象的写步**方向不同 ⇒ 换成车端那条；
计划里没有这个对象（问句被车端误判成命令的那一轮）一个字不动。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.edge_authority import direction_conflicts
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import Step
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore


def _step(intent, agent="vehicle"):
    return Step(id="s1", agent_id=agent, intent=intent)


def test_only_a_write_on_the_same_object_with_another_direction_conflicts():
    assert direction_conflicts([_step("trunk.open")], "trunk.close") == [0]
    assert direction_conflicts([_step("trunk.close")], "trunk.close") == []           # 一致
    assert direction_conflicts([_step("manual.query", "manual")], "trunk.open") == []  # 计划里没有这个对象
    assert direction_conflicts([_step("trunk.query")], "trunk.open") == []             # 读步不改成写
    assert direction_conflicts([_step("window.open")], "trunk.close") == []            # 别的对象
    assert direction_conflicts([_step("trunk.open")], "") == []                        # 没盖章
    assert direction_conflicts([_step("trunk.open")], "trunk.query") == []             # 车端解析本身是读
    assert direction_conflicts([_step("chitchat.talk", "chitchat"), _step("door_lock.close")],
                               "door_lock.open") == [1]


# ── 引擎端到端：进程内替身，车控 Agent 三条能力（编号按 (agent, intent) 排序：trunk.close=1、trunk.open=2、window.open=3）──

class _Cap:
    def __init__(self, intent, slots, description):
        self.intent, self.slots, self.description = intent, slots, description
        self.require_confirm = False


def _vehicle_agent():
    manifest = SimpleNamespace(
        agent_id="vehicle", trust_level="first_party", latency_budget_ms=2000, requires_permissions=[],
        capabilities=[_Cap("trunk.close", [], "关闭后备箱"), _Cap("trunk.open", [], "打开后备箱"),
                      _Cap("window.open", ["position"], "打开车窗")])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50070")


_REFS = {"trunk.close": "cap_0001", "trunk.open": "cap_0002", "window.open": "cap_0003"}


class _Resp:
    def __init__(self, status=0, speech=""):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions, self.ui_card, self.data, self.missing_slots = [], None, None, []


class _Spy:
    """车端替身：没确认 ⇒ NEED_CONFIRM（念出被派下来的那条），确认了 ⇒ 执行。"""

    def __init__(self, planned):
        self.planned = planned
        self.calls: list[tuple[str, str]] = []

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        confirmed = (meta or {}).get("confirmed") == "true"
        self.calls.append((intent, "confirmed" if confirmed else "asked"))
        return _Resp(speech=f"已执行{intent}。") if confirmed else _Resp(status=1, speech=f"要执行{intent}吗？")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return json.dumps({"goal": "关闭后备箱", "steps": [
                {"id": "s1", "capability_ref": _REFS[self.planned], "slots": {}, "depends_on": [], "slot_refs": {}}]})
        return "好的。"

    async def resolve(self, query="", intent="", top_k=1):
        return [_vehicle_agent()]

    async def list_agents(self):
        return [_vehicle_agent()]


def _engine(planned):
    spy = _Spy(planned)
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=SessionStore(redis_url=""))
    return engine, spy


def _run(engine, text, edge_confirm="", **extra):
    meta = {"_edge_confirm": edge_confirm} if edge_confirm else {}
    fields = {"is_confirmation": False, "operation_id": "", **extra}
    req = SimpleNamespace(text=text, session_id="sess-edge", request_id="r-edge", meta=meta,
                          context=SimpleNamespace(user_id="u1", vehicle_id="v1"), **fields)

    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def test_a_flipped_write_is_corrected_to_the_edges_direction_before_it_is_asked():
    """规划交了 `trunk.open`、车端盖章 `trunk.close` ⇒ 问的、确认后执行的都是关后备箱，开后备箱一次都没派出去。"""
    engine, spy = _engine("trunk.open")
    asked = _run(engine, "关闭后备箱", edge_confirm="trunk.close")[-1]
    assert asked.get("need_confirm") is True
    assert spy.calls == [("trunk.close", "asked")], spy.calls

    done = _run(engine, "确认", is_confirmation=True, operation_id=asked["operation_id"])[-1]
    assert not done.get("need_confirm")
    assert spy.calls[-1] == ("trunk.close", "confirmed")
    assert all(intent != "trunk.open" for intent, _ in spy.calls)


def test_without_the_edges_stamp_the_plan_is_untouched():
    """对照：没盖章（文字 / 规划器以外的路 / 旧车端）⇒ 行为同修前。"""
    engine, spy = _engine("trunk.open")
    _run(engine, "关闭后备箱")
    assert spy.calls == [("trunk.open", "asked")]


def test_a_plan_about_another_object_is_not_overridden():
    """车端把问句误判成命令时，规划选了别的能力——一个字不动（历史里「后备箱能放几个行李箱」那一轮）。"""
    engine, spy = _engine("window.open")
    _run(engine, "后备箱能放几个行李箱", edge_confirm="trunk.open")
    assert [intent for intent, _ in spy.calls] == ["window.open"]
