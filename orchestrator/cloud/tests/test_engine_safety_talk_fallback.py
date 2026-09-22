"""批 8 ②：会话里有未解除的安全告警时，planner 空手的轮不出「没听清」，改走兜底谈话（安全闸二第二臂）。

continuity 第一趟 T64/T65（`c4c1186d`）：T64「困到睁不开眼了，还要开两个小时」road-safety 劝停并把
`driver_state` 告警登记进焦点；T65「别提醒我，继续开就行」planner 两轮——第一轮抢救出一步
`cap_0138`（= vision.describe，映射表的最后一个键）、第二轮 `addressed=false, steps=[]`——engine 把
空计划送到「抱歉，我没听清您想让我做什么」。批 5 已记过一次同形态。用户刚刚拒绝了安全建议，
系统这一轮把安全这条线整个丢了；SF4 的考点恰是「拒绝之后那一轮」。第二、三趟同一句被规划成
`chitchat.talk`，chitchat 拿着 `focus_safety_alert` 答「立场不改」——那才是正确出口。

既有的安全闸二（`test_planning_safety_talk.py`）只认**这一句本身**是安全信号；这里补第二臂：
焦点里有**未解除且未过期**的告警（`safety_alert_active`，与 `_apply_focus_meta` 下发的是同一格）。
比第一臂再窄一格：只接零步且无澄清卡的那一路；这句已在解除告警（`alert_resolved`）不算前提。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from google.protobuf import struct_pb2
from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.context import Focus, WorkingSet
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore

from tests.test_planning import MockAgent

#: T65 的真实形态：模型判「非受话」、零步。`addressed=false` 在 planner 里是**合法空计划**、第一轮就被接受
#: （R4.4），文字输入的 engine 又不消费这个判定 ⇒ 直落「没听清」。（`addressed=true, steps=[]` 那种形态早被
#: `_no_action` 接住——第二次也这么说就交兜底 Agent——不经本闸，这里不拿它当证据。）
_NOT_ADDRESSED = '{"addressed":false,"steps":[]}'
_CLARIFY = ('{"addressed":true,"steps":[],"clarify":{"question":"你想让我怎么处理？",'
            '"options":[{"label":"找服务区","send_text":"找最近的服务区"},'
            '{"label":"放点音乐","send_text":"放点音乐"}]}}')


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"], response_only=("chitchat.talk",)),
            MockAgent("navigation", ["navigation.navigate_to"])]


def _alert(age_s: float = 0.0) -> dict:
    return {"level": "critical", "signal": "驾驶员困倦", "ts": int(time.time() - age_s)}


def _build(text: str, reply: str, *, focus_alert: dict | None):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    focus = Focus(safety_alert=dict(focus_alert)) if focus_alert else None
    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents(), focus=focus),
                                     PlanContext(session_id="t")))


def test_empty_plan_under_an_active_focus_alert_becomes_a_talk():
    plan = _build("别提醒我，继续开就行", _NOT_ADDRESSED, focus_alert=_alert())
    assert [s.intent for s in plan.steps] == ["chitchat.talk"], plan
    assert plan.steps[0].response_only is True
    assert (plan.plan_mode or "").endswith("_safety_talk"), plan.plan_mode


def test_without_a_focus_alert_the_empty_plan_stays_empty():
    """没有安全前提 ⇒ 行为逐字不变（engine 仍出「没听清」）。"""
    plan = _build("别提醒我，继续开就行", _NOT_ADDRESSED, focus_alert=None)
    assert not plan.steps
    assert not (plan.plan_mode or "").endswith("_safety_talk")


def test_an_expired_focus_alert_is_not_a_premise():
    plan = _build("别提醒我，继续开就行", _NOT_ADDRESSED, focus_alert=_alert(age_s=3 * 3600))
    assert not plan.steps


def test_resolving_the_alert_in_this_utterance_is_not_a_premise():
    plan = _build("机油灯已经灭了，恢复正常了", _NOT_ADDRESSED, focus_alert=_alert())
    assert not plan.steps


def test_a_clarify_card_survives_the_focus_arm():
    """第二臂只接零步且无澄清卡：告警在会话里，这一句的澄清卡仍是模型的（第一臂照旧会盖掉）。"""
    plan = _build("导航去那家", _CLARIFY, focus_alert=_alert())
    assert not plan.steps and plan.clarify is not None
    assert not (plan.plan_mode or "").endswith("_safety_talk")


def test_the_voice_receipt_verdict_is_left_to_the_engine():
    """接管不改 `addressed`：免唤醒语音源里模型判非受话的背景话，engine 照旧静默拒识。"""
    plan = _build("别提醒我，继续开就行", _NOT_ADDRESSED, focus_alert=_alert())
    assert plan.steps and plan.addressed is False


# ── 端到端：真 PlanBuilder + engine，final 不再是「没听清」，告警随 meta 下发给兜底 Agent ──

class _Clients:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def list_agents(self):
        return _agents()

    async def resolve(self, query="", intent="", top_k=1):
        return _agents()

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        return agent_pb2.ExecuteResponse(
            status=agent_pb2.ExecuteResponse.OK, data=struct_pb2.Struct(),
            speech="提醒我不会撤——请就近停下，哪怕只是几分钟也好。")

    async def call_agent_stream(self, *args, **kwargs):
        if False:
            yield None


def test_engine_answers_with_the_alert_as_premise_instead_of_not_understood():
    clients = _Clients()

    async def llm(messages, **kwargs):
        return _NOT_ADDRESSED if "任务编排器" in messages[0]["content"] else "done"

    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=clients, planner=PlanBuilder(llm_fn=llm, registry_fn=clients.resolve),
        executor=DagExecutor(call_agent_fn=clients.call_agent),
        aggregator=Aggregator(llm), session=session)
    asyncio.run(session.save_focus("sess-sf", {"safety_alert": _alert(), "focus_ts": time.time()},
                                   owner_user_id="u1"))
    request = SimpleNamespace(
        text="别提醒我，继续开就行", session_id="sess-sf", request_id="r-sf", is_confirmation=False,
        operation_id="", meta={}, context=SimpleNamespace(user_id="u1", vehicle_id="v1"))

    async def collect():
        return [e async for e in engine.run(request)]
    final = asyncio.run(collect())[-1]

    assert clients.calls and clients.calls[0][0] == "chitchat.talk"
    assert "没听清" not in final["speech"] and "停下" in final["speech"], final
    assert "驾驶员困倦" in clients.calls[0][1].get("focus_safety_alert", ""), clients.calls[0][1]
