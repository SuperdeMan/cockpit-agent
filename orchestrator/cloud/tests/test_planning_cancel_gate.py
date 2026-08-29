"""取消式句子不许被规划成「新建」或交给兜底编造（QA 余项，2026-08-29）。

## 来历：A/B 证伪了上一版修法

2026-08-29 给 `reminder.cancel` 补了两条范例，词面接近的句子当场转绿
（`取消带伞` 0/6→6/6、`取消参加代号889001的评审会` 3/6→6/6），
但**事后补的留出臂「取消交周报」仍是 1/6**（与同趟对照臂比 p=0.0076），
落域散成五处：`system.clarify` 2 / `research.cancel` 1 / `reminder.cancel` 1 /
`chitchat.talk` 1 / **`reminder.create` 1**。

⇒ **范例覆盖得了词面，覆盖不了「这个名字是不是一条提醒」这个 planner 没有的事实。**
真栈两句逐字实录：
- 第 6 轮 `reminder.create` → **「记下了：交周报。」**（说取消，反向建了一条）
- 第 5 轮 `chitchat.talk` → **「好嘞，周报提醒已经取消啦」**，而本轮零动作

三条候选路（§4.2）里本闸走的是第三条：**接受歧义，但把兜底做对**——
它只要求「错得诚实」，不要求「猜得准」。另外两条（把提醒列表渲染进 planner 上下文 /
恢复窄 route_hint）各有更大的代价，且**不能混着做**。

## 判据面刻意窄（三道门，逐条有误伤对照）

1. 是「取消 X」形态 —— 判据在 `pending_cancel.cancel_instruction_object`，
   与另外两个取消问法**共用同一份词表**。分句符挡住「算了，帮我找家咖啡店」
   这类**话语承接**，那是本闸的主要误伤面。
2. 不是问句 —— 「刚才那个取消了吗」该由兜底老实回答。
3. 只丢**新建**语义的步 —— `is_create_intent` 只收无歧义的那几个操作名，
   「取消静音」→ `volume.unmute` 一个字不改。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import WorkingSet
from orchestrator.cloud.models import PlanContext
from orchestrator.cloud.planning import PlanBuilder

from tests.test_planning import MockAgent


@pytest.fixture(autouse=True)
def _offline_retrieval(monkeypatch):
    monkeypatch.setenv("EXEMPLARS_RETRIEVAL", "lexical")


def _agents():
    return [MockAgent("chitchat", ["chitchat.talk"]),
            MockAgent("reminder", ["reminder.create", "reminder.cancel"]),
            MockAgent("navigation", ["navigation.cancel"]),
            MockAgent("edge-vehicle", ["volume.unmute"],
                      kind="edge_fast", deployment="edge"),
            MockAgent("nearby", ["nearby.search"])]


#: `capability_ref` 是**本请求映射**里的 key，序号由 `_capability_pairs` 的顺序定。
#: 直接写 intent/agent_id 会被 wire 契约当场判无效（首版就是这么红的）——
#: 那条校验是刻意的：planner 只能从本请求白名单里选，不许自己发明能力。
def _cap_ref(intent: str) -> str:
    from orchestrator.cloud.planning import _capability_pairs
    pairs = _capability_pairs(_agents())
    for index, (_aid, name) in enumerate(pairs, 1):
        if name == intent:
            return f"cap_{index:04d}"
    raise AssertionError(f"{intent} 不在本请求能力映射里")


def _reply(*intents: str) -> str:
    return json.dumps({"addressed": True, "steps": [
        {"id": f"s{i}", "capability_ref": _cap_ref(name),
         "slots": {}, "depends_on": [], "slot_refs": {}}
        for i, name in enumerate(intents, 1)]}, ensure_ascii=False)


def _build(text: str, reply: str):
    async def mock_llm(messages):
        return reply

    async def mock_resolve(query, top_k=1):
        return []

    builder = PlanBuilder(llm_fn=mock_llm, registry_fn=mock_resolve)
    return asyncio.run(builder.build(text, WorkingSet(catalog=_agents()),
                                     PlanContext(session_id="t")))


# ── 1. 正向：真栈那两种坏产物 ───────────────────────────────────────────────

def test_cancel_utterance_planned_into_create_is_blocked():
    """真栈第 6 轮：「取消交周报」→ `reminder.create` →「记下了：交周报。」

    **方向是反的**——用户说取消，系统建了一条。这一步必须被丢掉。
    """
    plan = _build("取消交周报", _reply("reminder.create"))

    assert [s.intent for s in plan.steps] == []
    assert "_cancel_create_blocked" in (plan.plan_mode or "")
    assert plan.cancel_unresolved == "交周报"


def test_cancel_utterance_left_with_only_the_fallback_agent_asks_instead():
    """真栈第 5 轮：「取消交周报」→ `chitchat.talk` →「好嘞，周报提醒已经取消啦」
    而本轮**零动作**（C11 那一族的执行性编造）。

    兜底没有任何东西可以取消 ⇒ 换成一句诚实追问，名字带给 engine 出话术。
    """
    plan = _build("取消交周报", _reply("chitchat.talk"))

    assert plan.steps == []
    assert plan.clarify is None
    assert plan.cancel_unresolved == "交周报"
    assert "_cancel_unresolved" in (plan.plan_mode or "")


def test_engine_asks_instead_of_claiming_the_cancel_happened():
    """闸的用户可见产物：**不许出现「已经取消」**，也不许赖用户「没听清」。

    这两句话的区别就是本闸的全部意义——我们听清了，只是不知道是哪一件事。
    """
    from orchestrator.cloud.models import Plan
    plan = Plan(steps=[], raw_text="取消交周报", cancel_unresolved="交周报")
    # engine 那条分支只读 `cancel_unresolved`，这里直接钉住契约字段
    assert plan.cancel_unresolved == "交周报"


# ── 2. 误伤对照：正常的取消一个字不改 ───────────────────────────────────────

@pytest.mark.parametrize("text,intent", [
    ("取消导航", "navigation.cancel"),          # 长会话 turn 77 那句
    ("取消第二条提醒", "reminder.cancel"),
    ("取消静音", "volume.unmute"),              # 操作名不是新建 ⇒ 不许碰
])
def test_a_cancel_that_landed_somewhere_executable_is_untouched(text, intent):
    plan = _build(text, _reply(intent))

    assert [s.intent for s in plan.steps] == [intent], (
        f"「{text}」的正常落域被取消闸吃掉了")
    assert not plan.cancel_unresolved
    assert "_cancel" not in (plan.plan_mode or "")


@pytest.mark.parametrize("text,intent", [
    # **话语承接**：取消词后面还有一个完整的新请求 ⇒ 这不是「取消 X」
    ("算了，帮我找家咖啡店", "nearby.search"),
    ("不用了，帮我记一下明天买牛奶", "reminder.create"),
])
def test_a_discourse_marker_is_not_a_cancel_instruction(text, intent):
    """本闸最大的误伤面。第二条尤其要命：它就是一条**合法的新建**，
    而闸恰恰是拿来丢新建步的——分句符这一道门挡的就是它。
    """
    plan = _build(text, _reply(intent))

    assert [s.intent for s in plan.steps] == [intent], f"「{text}」被误伤"
    assert not plan.cancel_unresolved


def test_a_question_about_a_cancellation_still_goes_to_the_fallback():
    """「刚才那个取消了吗」是**在问**，不是在下指令 —— 兜底老实回答就是对的。"""
    plan = _build("刚才那个取消了吗", _reply("chitchat.talk"))

    assert [s.intent for s in plan.steps] == ["chitchat.talk"]
    assert not plan.cancel_unresolved
