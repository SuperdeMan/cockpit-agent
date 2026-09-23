"""确认授权的两个必要条件（评审三轮 R3-01，2026-09-23）。

A. **明确肯定**：「啊 / 唉 / 请 / 那 / 。」这类只有语气面的话剥空了也不是授权——必须至少吃到一个肯定词。
   修前 `_bare_affirmation` 剥空即 True，唯一一条 `wait_confirm` 下走 `kind=one` ⇒ `_restore(inject_confirmed=True)`。
B. **目标兼容**：点名确认的二元片段只负责**召回**候选；授权还要过**裁决**——点名的每个字都要落在挂起那一刻
   服务端生成的已校验步骤摘要（能力描述 + 槽值，确认卡上的同一句）里，数字按整串比。修前挂着「打开后备箱」时
   「确认关闭后备箱」「确认锁上后备箱」「确认打开车窗」都靠共享片段命中唯一候选而把后备箱打开了。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.models import SessionState
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore

# ── 进程内替身：一个车控 Agent，三条能力，描述是中文人话（Registry 的真实形态）──────────
# capability_ref 按 (agent_id, intent) 排序编号：trunk.close=1、trunk.open=2、window.open=3


class _Cap:
    def __init__(self, intent, slots, description, require_confirm=False):
        self.intent, self.slots, self.description = intent, slots, description
        self.require_confirm = require_confirm


def _vehicle_agent():
    manifest = SimpleNamespace(
        agent_id="vehicle", trust_level="first_party", latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[
            _Cap("trunk.close", [], "关闭后备箱", True),
            _Cap("trunk.open", [], "打开后备箱", True),
            _Cap("window.open", ["position"], "打开车窗", True),
        ])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50070")


_REFS = {"trunk.close": "cap_0001", "trunk.open": "cap_0002", "window.open": "cap_0003"}


class _Resp:
    def __init__(self, status=0, speech="", actions=None):
        self.status, self.speech, self.follow_up = status, speech, ""
        self.actions = actions or []
        self.ui_card, self.data, self.missing_slots = None, None, []


class _Spy:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.plan_calls = 0
        self.next_plan: dict = {"intent": "trunk.open", "slots": {}, "goal": "打开后备箱"}
        self.registry_up = True

    def confirmed(self, intent: str) -> int:
        return sum(1 for i, m in self.calls if i == intent and m.get("confirmed") == "true")

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, dict(meta or {})))
        if (meta or {}).get("confirmed") == "true":
            return _Resp(speech=f"已执行{intent}。", actions=[SimpleNamespace(
                type="vehicle.control", payload={"command": intent}, require_confirm=False)])
        return _Resp(status=1, speech="这是危险动作，确认执行吗？")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            self.plan_calls += 1
            p = self.next_plan
            return json.dumps({"goal": p["goal"], "steps": [
                {"id": "s1", "capability_ref": _REFS[p["intent"]], "slots": p["slots"],
                 "depends_on": [], "slot_refs": {}}]})
        return "好的。"

    async def resolve(self, query="", intent="", top_k=1):
        return [_vehicle_agent()]

    async def list_agents(self):
        if not self.registry_up:
            raise RuntimeError("registry down")
        return [_vehicle_agent()]


def _make_engine():
    spy = _Spy()
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session)
    return engine, spy, session


def _req(text, *, is_confirmation=False, operation_id="", edge_nlu=""):
    meta = {"_edge_nlu": edge_nlu} if edge_nlu else {}
    return SimpleNamespace(
        text=text, session_id="sess-1", request_id="r1", meta=meta,
        is_confirmation=is_confirmation, operation_id=operation_id,
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"))


def _run(engine, req) -> list[dict]:
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


def _suspend_trunk(engine, spy) -> str:
    spy.next_plan = {"intent": "trunk.open", "slots": {}, "goal": "打开后备箱"}
    final = _run(engine, _req("打开后备箱"))[-1]
    assert final.get("need_confirm") and final.get("operation_id")
    return final["operation_id"]


def _alive(session, op) -> bool:
    return asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op)) is not None


# ── A. 明确肯定 ─────────────────────────────────────────────────────────────

_FILLERS = ["啊", "唉", "请", "那", "。", "的", "了", "哈", "就", "一下", "哎", "那就", "请，", "嘛"]


@pytest.mark.parametrize("text", _FILLERS)
def test_filler_alone_is_not_an_affirmation(text):
    assert PlannerEngine._bare_affirmation(text) is False, text
    assert PlannerEngine._confirm_reply(text, False) is None, text
    assert PlannerEngine._is_bare_confirm_word(text) is False, text
    pending = SessionState(phase="wait_confirm", operation_id="op-1",
                           pending_plan={"goal": "打开后备箱"})
    assert PlannerEngine._resolve_spoken_confirm(text, False, [pending]).kind == "", text


@pytest.mark.parametrize("text", ["啊", "唉", "请", "那", "。", "一下"])
def test_filler_alone_never_injects_confirmed(text):
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    final = _run(engine, _req(text))[-1]
    assert spy.confirmed("trunk.open") == 0, text
    assert op not in (final.get("closed_operation_ids") or []), text
    assert _alive(session, op), text


@pytest.mark.parametrize("text", ["确认", "好的，确认吧", "嗯可以", "嗯", "嗯嗯", "好", "行啊",
                                  "ok", "那就确认吧", "请确认", "哎，好的", "确认了", "好吧"])
def test_real_affirmations_still_confirm(text):
    assert PlannerEngine._bare_affirmation(text) is True, text
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    final = _run(engine, _req(text))[-1]
    assert spy.confirmed("trunk.open") == 1, text
    assert op in (final.get("closed_operation_ids") or []), text


def test_a_filler_without_any_pending_goes_to_the_planner_not_the_no_pending_exit():
    engine, spy, _ = _make_engine()
    final = _run(engine, _req("啊"))[-1]
    assert "没有待确认" not in (final.get("speech") or "")
    assert spy.plan_calls == 1


# ── B. 目标兼容：召回 ≠ 授权 ───────────────────────────────────────────────

def _state(op, summary, intent, goal=None, slots=None):
    return SessionState(
        phase="wait_confirm", operation_id=op, owner_user_id="u1", pending_step_id="s1",
        action_summary=summary,
        pending_plan={"goal": goal or summary, "raw_text": goal or summary,
                      "steps": [{"id": "s1", "agent_id": "vehicle", "intent": intent,
                                 "slots": slots or {}, "depends_on": [], "slot_refs": {}}]})


@pytest.mark.parametrize("text", ["确认关闭后备箱", "确认锁上后备箱", "确认打开车窗",
                                  "确认不打开后备箱", "确认后备箱关上"])
def test_a_named_confirm_that_contradicts_the_pending_step_is_not_one(text):
    trunk = _state("op-1", "打开后备箱", "trunk.open")
    spoken = PlannerEngine._resolve_spoken_confirm(text, False, [trunk])
    assert spoken.kind == "mismatch", (text, spoken.kind)
    assert spoken.target is None


@pytest.mark.parametrize("text", ["确认打开后备箱", "确认后备箱", "确认把后备箱打开",
                                  "确认一下那个打开后备箱的", "确认开后备箱"])
def test_a_named_confirm_that_names_the_pending_step_still_confirms(text):
    trunk = _state("op-1", "打开后备箱", "trunk.open")
    spoken = PlannerEngine._resolve_spoken_confirm(text, False, [trunk])
    assert spoken.kind == "one" and spoken.target is trunk, (text, spoken.kind)


def test_position_and_amount_must_match_as_whole_facts():
    rear_right = _state("op-1", "打开车窗（右后）", "window.open", slots={"position": "右后"})
    assert PlannerEngine._resolve_spoken_confirm(
        "确认打开左后车窗", False, [rear_right]).kind == "mismatch"
    assert PlannerEngine._resolve_spoken_confirm(
        "确认打开右后车窗", False, [rear_right]).kind == "one"
    pay = _state("op-2", "支付订单（30元）", "shop.pay", slots={"amount": "30元"})
    assert PlannerEngine._resolve_spoken_confirm("确认支付50元", False, [pay]).kind == "mismatch"
    assert PlannerEngine._resolve_spoken_confirm("确认支付300元", False, [pay]).kind == "mismatch"
    assert PlannerEngine._resolve_spoken_confirm("确认支付30元", False, [pay]).kind == "one"
    assert PlannerEngine._resolve_spoken_confirm("确认支付三十元", False, [pay]).kind == "one"


def test_single_characters_never_carry_a_naming():
    """「锁」「车」「门」每个字都在「打开车门锁（全车）」里，但「锁车门」说的是反方向——片段至少两字，
    单字不作数（否则逐字都覆盖得到，裁决退化成字符集包含）。"""
    unlock = _state("op-1", "打开车门锁（全车）", "door_lock.open", slots={"positions": "全车"})
    assert PlannerEngine._resolve_spoken_confirm("确认锁车门", False, [unlock]).kind == "mismatch"
    assert PlannerEngine._resolve_spoken_confirm("确认车门锁上", False, [unlock]).kind == "mismatch"
    assert PlannerEngine._resolve_spoken_confirm("确认打开全车车门锁", False, [unlock]).kind == "one"


def test_the_llm_goal_and_the_origin_text_are_recall_only_never_the_verdict():
    """goal / 原话里有「关闭车窗」（用户原话说了别关车窗），挂起的步是打开后备箱：
    「确认关闭车窗」召回得到它，但裁决面只认已校验的步骤摘要 ⇒ 不是授权。"""
    trunk = _state("op-1", "打开后备箱", "trunk.open", goal="打开后备箱，别关闭车窗")
    assert PlannerEngine._resolve_spoken_confirm("确认关闭车窗", False, [trunk]).kind == "mismatch"


def test_no_verified_summary_means_no_named_authorization():
    bare = _state("op-1", "", "trunk.open", goal="打开后备箱")
    assert PlannerEngine._resolve_spoken_confirm("确认打开后备箱", False, [bare]).kind == "mismatch"


def test_the_edge_parse_of_the_naming_can_veto_but_never_grant():
    trunk = _state("op-1", "打开后备箱", "trunk.open")
    assert PlannerEngine._resolve_spoken_confirm(
        "确认后备箱", False, [trunk], edge_intent="trunk.close").kind == "mismatch"
    assert PlannerEngine._resolve_spoken_confirm(
        "确认后备箱", False, [trunk], edge_intent="trunk.open").kind == "one"
    # 端侧解析与这一步相同也不能替摘要授权
    bare = _state("op-2", "", "trunk.open", goal="打开后备箱")
    assert PlannerEngine._resolve_spoken_confirm(
        "确认打开后备箱", False, [bare], edge_intent="trunk.open").kind == "mismatch"


def test_two_compatible_pendings_are_asked_not_guessed():
    a = _state("op-a", "打开车窗（左后）", "window.open", slots={"position": "左后"})
    b = _state("op-b", "打开车窗（右后）", "window.open", slots={"position": "右后"})
    assert PlannerEngine._resolve_spoken_confirm("确认打开车窗", False, [a, b]).kind == "ambiguous"
    only_b = PlannerEngine._resolve_spoken_confirm("确认打开右后车窗", False, [a, b])
    assert only_b.kind == "one" and only_b.target is b


def test_a_naming_that_recalls_nothing_is_still_an_interjection():
    trunk = _state("op-1", "打开后备箱", "trunk.open")
    assert PlannerEngine._resolve_spoken_confirm("确认订单", False, [trunk]).kind == "named_miss"


# ── B. engine 端到端 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["确认关闭后备箱", "确认锁上后备箱", "确认打开车窗"])
def test_engine_a_contradicting_named_confirm_executes_nothing_and_keeps_the_pending(text):
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    plans_before = spy.plan_calls
    final = _run(engine, _req(text))[-1]
    assert spy.confirmed("trunk.open") == 0, text
    assert not final.get("actions"), text
    assert op not in (final.get("closed_operation_ids") or []), text
    assert final.get("held_operation_ids") == [op], text
    assert "打开后备箱" in final["speech"] and "没有执行" in final["speech"], text
    assert spy.plan_calls == plans_before, text               # 确定性出口，零 LLM
    assert _alive(session, op), text
    # 随后一句裸「确认」仍然确认的是它
    final = _run(engine, _req("确认"))[-1]
    assert spy.confirmed("trunk.open") == 1
    assert op in (final.get("closed_operation_ids") or [])


def test_engine_a_matching_named_confirm_executes_it():
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    final = _run(engine, _req("确认打开后备箱"))[-1]
    assert spy.confirmed("trunk.open") == 1
    assert op in (final.get("closed_operation_ids") or [])


def test_engine_stamps_the_verified_summary_on_the_pending_state():
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op))
    assert state.action_summary == "打开后备箱"


def test_engine_a_legacy_pending_without_summary_is_described_on_the_fly():
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op))
    state.action_summary = ""                                  # 旧记录：没有这一格
    asyncio.run(session.save("sess-1", state))
    _run(engine, _req("确认打开后备箱"))
    assert spy.confirmed("trunk.open") == 1


def test_engine_a_legacy_pending_fails_closed_when_the_registry_is_down():
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    state = asyncio.run(session.load("sess-1", owner_user_id="u1", operation_id=op))
    state.action_summary = ""
    asyncio.run(session.save("sess-1", state))
    spy.registry_up = False
    final = _run(engine, _req("确认打开后备箱"))[-1]
    assert spy.confirmed("trunk.open") == 0
    assert _alive(session, op)
    assert "没有执行" in final["speech"]


def test_engine_the_edge_parse_vetoes_a_named_confirm():
    engine, spy, session = _make_engine()
    op = _suspend_trunk(engine, spy)
    _run(engine, _req("确认后备箱", edge_nlu="trunk.close|0.90"))
    assert spy.confirmed("trunk.open") == 0
    assert _alive(session, op)


def test_engine_bare_confirm_is_unaffected_by_the_edge_parse():
    """裸「确认」没有点名余量，端侧解析与它无关（按钮 / 裸确认的通道一字不变）。"""
    engine, spy, _ = _make_engine()
    _suspend_trunk(engine, spy)
    _run(engine, _req("确认", edge_nlu="trunk.close|0.90"))
    assert spy.confirmed("trunk.open") == 1


# ── B′. 点名取消：召回之后按覆盖度排序 ─────────────────────────────────────

def test_named_cancel_prefers_the_uniquely_best_covered_pending():
    latte = _state("op-l", "下单（拿铁咖啡）", "shop.order", goal="订一杯拿铁咖啡")
    americano = _state("op-a", "下单（美式咖啡）", "shop.order", goal="订一杯美式咖啡")
    best = PlannerEngine._best_named([latte, americano], "拿铁咖啡")
    assert best == [latte]
    tied = PlannerEngine._best_named([latte, americano], "那杯咖啡")
    assert set(id(s) for s in tied) == {id(latte), id(americano)}


def test_engine_named_cancel_binds_the_best_covered_pending_instead_of_asking():
    engine, spy, session = _make_engine()
    for op, goal in (("op-l", "订一杯拿铁咖啡"), ("op-a", "订一杯美式咖啡")):
        asyncio.run(session.save("sess-1", _state(op, "", "shop.order", goal=goal)))
    final = _run(engine, _req("取消刚才拿铁咖啡"))[-1]
    assert "op-l" in (final.get("closed_operation_ids") or [])
    assert _alive(session, "op-a") and not _alive(session, "op-l")
