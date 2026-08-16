"""执行事实 → 焦点对象（QA 卡 Q7 的两条残余：EL1 跨轮省略 / OR2 同轮跨段）。

## 它修的是什么

`Focus` 只由**云侧规划轮**构建（`update_focus(plan, results)`）。端侧本地快路径
那 40% 的车控动作根本不上云——真栈对照实测：跑「打开天窗」（端侧本地）之后
`planner:focus:*` **0 个 key**，跑「附近有什么好吃的」（云侧规划轮）之后 **1 个**。

于是下一轮说「不用了，关掉」时 planner 手里一个对象都没有，只能从对话文本猜。
三次取样三个样：**无动作却答「好的，关上了」** / **反向执行 `sunroof.open`** / 正确。

Q6 其实已经把事实源建好了（`AppendTurn.actions`，端侧本地轮与云侧规划轮各写各的），
proto 读侧字段也加了、注释逐字写着「读侧必须也带上——**存下来而读不到等于没存**」，
`agents/_sdk/clients.py` 照做了——**只有 `orchestrator/cloud/clients.py` 没读**。
同一个 proto 的两份客户端实现，一份读了一份没读。

> 判据：**读写对称要逐个消费方验，不是「写侧加了字段」就算通了。**

## 两条纪律钉在下面的用例里

1. **最新执行事实覆盖陈旧控制焦点**——历史按轮次有序，最近一条成功动作比 Redis 中
   可能由更早云侧轮次留下的 `focus.obj` 更新；否则「先调氛围灯、再本地开天窗」后
   的「关掉」仍会错指氛围灯。
2. **同轮压过跨轮**——`edge_executed` 是这一轮端侧刚执行掉的，比历史里任何一轮都近。
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.context import (  # noqa: E402
    Focus, augment_focus_with_execution, recent_control_execution)


# ── 确定性纯函数：从执行事实解对象 ────────────────────────────────────────

def test_reads_the_object_from_the_previous_turns_executed_action():
    """EL1 的核心：上一轮端侧执行了 `sunroof.open`，这一轮就该知道对象是天窗。"""
    history = [
        {"role": "user", "text": "打开天窗", "actions": []},
        {"role": "assistant", "text": "好的", "actions": ["sunroof.open"]},
    ]
    assert recent_control_execution(history) == ("天窗", "开度", "sunroof.open")


def test_the_newest_control_action_wins():
    """多轮都有动作时取**最近**那条——焦点是「刚才」，不是「这个会话里某一次」。"""
    history = [
        {"role": "assistant", "text": "好的", "actions": ["sunroof.open"]},
        {"role": "assistant", "text": "好的", "actions": ["hvac.on"]},
    ]
    assert recent_control_execution(history)[0] == "空调"


def test_within_one_turn_the_last_control_action_wins():
    """同一轮多个动作时取最后一个——与 `extract_focus` 取「最近一个成功控制步」同口径。"""
    history = [{"role": "assistant", "actions": ["hvac.on", "sunroof.open"]}]
    assert recent_control_execution(history)[0] == "天窗"


def test_same_turn_edge_execution_outranks_any_history():
    """OR2 的核心：本轮端侧刚执行掉的，比历史里任何一轮都近。"""
    history = [{"role": "assistant", "actions": ["sunroof.open"]}]
    got = recent_control_execution(history, edge_executed=["hvac.off"])
    assert got == ("空调", "温度", "hvac.off")


def test_non_control_actions_are_not_a_focus_object():
    """导航/搜索类动作不落车控焦点——`_CONTROL_FOCUS` 之外的域一律解不出。"""
    history = [{"role": "assistant", "actions": ["navigation.navigate_to"]}]
    assert recent_control_execution(history) is None


def test_legacy_turns_without_actions_are_simply_absent():
    """本字段之前写入的存量轮次没有 `actions` 键——**不许抛**，就是解不出。"""
    assert recent_control_execution([{"role": "user", "text": "打开天窗"}]) is None
    assert recent_control_execution([]) is None
    assert recent_control_execution(None) is None


def test_untrusted_shapes_are_survived_not_trusted():
    """history 来自 gRPC，形状不可信。**防到真正会被拿去 split 的那个值**
    （CLAUDE.md §6），不是防到最外层容器为止。"""
    history = [
        None, "not-a-dict", 42,
        {"role": "assistant", "actions": "hvac.on"},          # 非 list
        {"role": "assistant", "actions": [None, 7, "", "  "]},  # 元素非 str / 空白
        {"role": "assistant", "actions": ["sunroof.open"]},
    ]
    assert recent_control_execution(history) == ("天窗", "开度", "sunroof.open")


def test_a_bare_action_name_without_a_dot_is_not_a_control_intent():
    assert recent_control_execution([{"actions": ["hvac"]}])[0] == "空调"
    assert recent_control_execution([{"actions": ["unknown_thing"]}]) is None


# ── 注入 Focus：只填空，不改写 ─────────────────────────────────────────────

def test_focus_is_created_when_there_was_none():
    """端侧本地轮压根不写云侧焦点 ⇒ 这里拿到的 focus 就是 None。"""
    history = [{"role": "assistant", "actions": ["sunroof.open"]}]
    focus = augment_focus_with_execution(None, history)
    assert focus is not None
    assert (focus.obj, focus.attr, focus.last_intent) == ("天窗", "开度", "sunroof.open")


def test_a_newer_execution_fact_overwrites_a_stale_control_focus():
    """云侧焦点可能比后来的本地快路径旧，最近执行事实必须赢。

    真实顺序可以是：云侧调氛围灯（写 Focus）→端侧开天窗（只写 actions）→「关掉」。
    若无条件保留已有对象，EL1 只是从「没有对象」变成「稳定拿错旧对象」。
    """
    focus = Focus(obj="氛围灯", attr="颜色", last_intent="ambient.set")
    history = [{"role": "assistant", "actions": ["sunroof.open"]}]
    out = augment_focus_with_execution(focus, history)
    assert (out.obj, out.attr, out.last_intent) == ("天窗", "开度", "sunroof.open")


def test_an_existing_focus_without_a_control_object_is_filled_in():
    """有焦点但没有车控对象（比如上一轮是导航）⇒ 该填还是要填，其余字段不动。"""
    focus = Focus(last_destination="深圳湾公园")
    out = augment_focus_with_execution(focus, [{"actions": ["hvac.on"]}])
    assert out.obj == "空调"
    assert out.last_destination == "深圳湾公园"


def test_last_intent_tracks_the_same_newest_execution_as_the_object():
    """对象与意图必须来自同一事实，否则省略守卫会按旧 namespace 拒绝正确计划。"""
    focus = Focus(last_intent="nearby.search")
    out = augment_focus_with_execution(focus, [{"actions": ["hvac.on"]}])
    assert out.obj == "空调" and out.last_intent == "hvac.on"


def test_replacing_a_stale_control_focus_clears_unprovable_position_and_agent():
    """动作账本只有意图名，没有位置/agent；旧焦点的两格不得粘到新对象上。"""
    focus = Focus(obj="座椅", positions=["副驾"], last_agent_id="vehicle-control")
    out = augment_focus_with_execution(focus, [{"actions": ["sunroof.open"]}])
    assert out.obj == "天窗"
    assert out.positions == []
    assert out.last_agent_id == ""


def test_nothing_to_add_leaves_the_focus_exactly_as_it_was():
    """**fail-open**：解不出就退化成本机制存在之前的行为，绝不制造一个空焦点。"""
    assert augment_focus_with_execution(None, []) is None
    focus = Focus(last_poi="瑞幸")
    assert augment_focus_with_execution(focus, [{"actions": ["x.y"]}]) is focus


# ── 装配面：assemble 真的把它接上了吗 ─────────────────────────────────────

class _Ctx:
    session_id = "s1"
    user_id = "u1"
    occupant_id = "primary"
    edge_executed: list[str] = []


class _Clients:
    """只实现 assemble 用得到的三件事；`get_session` 逐字照真实客户端**带 actions**。"""

    def __init__(self, history):
        self._history = history

    async def get_session(self, session_id, last_n=6, *, user_id="", occupant_id=""):
        return list(self._history)

    async def recall(self, user_id, query="", **kw):
        return []

    async def list_agents(self):
        return []


def _assemble(history, edge_executed=()):
    from orchestrator.cloud.context import ContextManager
    ctx = _Ctx()
    ctx.edge_executed = list(edge_executed)
    cm = ContextManager(_Clients(history))
    return asyncio.run(cm.assemble("不用了，关掉", ctx)).focus


def test_assemble_wires_the_execution_fact_into_the_focus():
    """**覆盖面守卫**：函数写对了但没接上，症状与没写完全相同——无日志、无报错、
    只是不发生（Q12 那次 `loop.py` 调了函数却漏传 `ctx` 的同款形态）。"""
    focus = _assemble([{"role": "assistant", "actions": ["sunroof.open"]}])
    assert focus is not None and focus.obj == "天窗"


def test_assemble_carries_the_same_turn_edge_execution():
    """OR2 那条通道：本轮端侧执行的动作经 `ctx.edge_executed` 进来，历史里可以什么都没有。"""
    focus = _assemble([], edge_executed=["hvac.off"])
    assert focus is not None and focus.obj == "空调"


def test_assemble_stays_none_when_there_is_no_execution_fact():
    assert _assemble([{"role": "user", "text": "你好"}]) is None


# ── 读写对称守卫：真实客户端有没有把 actions 带回来 ───────────────────────

class _MemoryStubWithActions:
    """按 proto 造一个真 `GetSessionResponse`——**不用假 dict**，
    否则守卫只证明了「我的替身有这个键」。"""

    async def GetSession(self, request, timeout=None):
        from cockpit.memory.v1 import memory_pb2
        return memory_pb2.GetSessionResponse(turns=[
            memory_pb2.Turn(role="user", text="打开天窗", exchange_id="x1"),
            memory_pb2.Turn(role="assistant", text="好的", exchange_id="x1",
                            actions=["sunroof.open"]),
        ])


def test_cloud_get_session_carries_the_executed_actions():
    """**Q6 的读写对称在云侧这一份客户端上兑现了没有。**

    Q6 把 `actions` 写进了 `AppendTurn`、proto 读侧字段也加了、
    `agents/_sdk/clients.py` 读了——**唯独 `orchestrator/cloud/clients.py` 没读**，
    于是云侧规划路径对「刚才执行了什么」完全失明（EL1 的直接成因）。

    ⚠ 这条断言**注入验红过**：把 `clients.py` 里那行 `"actions": list(t.actions)`
    去掉，本用例立刻红。恒绿的断言比没有更糟（§4.3）。
    """
    from orchestrator.cloud.clients import Clients
    clients = Clients()
    clients._memory_stub = lambda: _MemoryStubWithActions()   # type: ignore[method-assign]
    turns = asyncio.run(clients.get_session("s1", 6, user_id="u1"))
    assert [t.get("actions") for t in turns] == [[], ["sunroof.open"]]
    assert [t.get("exchange_id") for t in turns] == ["x1", "x1"]
    # 端到端：这份历史交给纯函数，必须解得出对象——两端接上了才算通。
    assert recent_control_execution(turns) == ("天窗", "开度", "sunroof.open")
