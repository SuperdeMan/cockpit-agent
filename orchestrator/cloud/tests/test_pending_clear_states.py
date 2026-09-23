"""挂起的**删除**也要说真话（评审三轮 R3-05，2026-09-23）。

二轮 R8 给读写补了三态，删除仍是布尔：初次读到挂起、删除那一刻后端不可用时 `clear` 返回 False，
`_close_pending` 不看返回值照样把它记进 `closed_operation_ids`，取消出口照样答「已为您取消」——而那条挂起
**还在存储里**，下一句「确认」就能把被取消的动作执行掉。同形态还有三处：`_settle_session`（确认执行完没删掉 ⇒
再说「确认」重复执行）、`_suspend` / `_suspend_clarify` 的「先删旧再存新」（存失败两头落空）。

修法：删除三态 + **进程内墓碑**（删不掉的那条在本进程里再也读不出来、确认 / 续接复活不了，后端恢复后的下一次
整表写把它真正删掉）；回执按能证明的状态说；「关旧开新」一次整表写。
"""
from __future__ import annotations

import asyncio

from orchestrator.cloud.models import SessionState
from orchestrator.cloud.session import (
    CLEAR_ABSENT, CLEAR_DELETED, CLEAR_UNAVAILABLE, PENDING_EMPTY, PENDING_FOUND,
    SAVE_OK, SAVE_UNAVAILABLE, SessionStore,
)

from .test_engine_confirm import _make_engine, _req, _run


def _state(op="op-1", phase="wait_confirm", goal="把全车门解锁"):
    return SessionState(phase=phase, owner_user_id="u1", operation_id=op,
                        pending_step_id="s1", completed_results={},
                        pending_plan={"goal": goal, "raw_text": goal})


class _FlakyStore(SessionStore):
    """内存后端 + 可开关的写故障：`fail_writes` 时整表写抛连接错误；`ack_lost` 时写生效但回执丢失。"""

    def __init__(self):
        super().__init__(redis_url="")
        self.fail_writes = False
        self.ack_lost = False
        self.writes = 0

    async def _write(self, r, key, owner, entries):
        self.writes += 1
        if self.fail_writes:
            raise ConnectionError("redis went away")
        written = await super()._write(r, key, owner, entries)
        if self.ack_lost:
            raise TimeoutError("reply lost after the write landed")
        return written


def _ops(store):
    entries, _state_ = asyncio.run(store.load_all_result("s1", owner_user_id="u1"))
    return [s.operation_id for s in entries]


# ── 存储层 ────────────────────────────────────────────────────────────────

def test_clear_result_distinguishes_deleted_absent_and_unavailable():
    store = _FlakyStore()
    asyncio.run(store.save("s1", _state()))
    assert asyncio.run(store.clear_result("s1", owner_user_id="u1", operation_id="op-1")) == CLEAR_DELETED
    assert asyncio.run(store.clear_result("s1", owner_user_id="u1", operation_id="op-1")) == CLEAR_ABSENT
    asyncio.run(store.save("s1", _state("op-2")))
    store.fail_writes = True
    assert asyncio.run(store.clear_result("s1", owner_user_id="u1", operation_id="op-2")) == CLEAR_UNAVAILABLE


def test_clear_keeps_its_bool_shape():
    store = _FlakyStore()
    asyncio.run(store.save("s1", _state()))
    assert asyncio.run(store.clear("s1", owner_user_id="u1", operation_id="op-1")) is True
    assert asyncio.run(store.clear("s1", owner_user_id="u1", operation_id="op-1")) is False


def test_an_undeleted_entry_is_tombstoned_and_never_read_back():
    store = _FlakyStore()
    asyncio.run(store.save("s1", _state("op-1")))
    asyncio.run(store.save("s1", _state("op-2")))
    store.fail_writes = True
    assert asyncio.run(store.clear_result("s1", owner_user_id="u1", operation_id="op-1")) == CLEAR_UNAVAILABLE
    store.fail_writes = False                                  # 后端恢复，记录仍在
    assert _ops(store) == ["op-2"]                             # 墓碑挡住，读不出来
    assert asyncio.run(store.load("s1", owner_user_id="u1", operation_id="op-1")) is None
    # 恢复后的第一次整表写把它真正删掉
    asyncio.run(store.save("s1", _state("op-3")))
    raw = store._mem[store._session_key("u1", "s1")][0]
    assert [s.operation_id for s in raw] == ["op-2", "op-3"]


def test_a_lost_reply_after_a_landed_delete_is_reported_unavailable_but_nothing_revives():
    store = _FlakyStore()
    asyncio.run(store.save("s1", _state("op-1")))
    store.ack_lost = True
    assert asyncio.run(store.clear_result("s1", owner_user_id="u1", operation_id="op-1")) == CLEAR_UNAVAILABLE
    store.ack_lost = False
    entries, state = asyncio.run(store.load_all_result("s1", owner_user_id="u1"))
    assert entries == [] and state == PENDING_EMPTY


def test_replacing_a_pending_is_one_write():
    store = _FlakyStore()
    asyncio.run(store.save("s1", _state("op-old", phase="wait_slot")))
    status, _ = asyncio.run(store.save_pending_result(
        "s1", _state("op-new"), replaces="op-old"))
    assert status == SAVE_OK and _ops(store) == ["op-new"]


def test_a_failed_replacement_neither_revives_the_old_nor_fakes_the_new():
    store = _FlakyStore()
    asyncio.run(store.save("s1", _state("op-old", phase="wait_slot")))
    store.fail_writes = True
    status, _ = asyncio.run(store.save_pending_result(
        "s1", _state("op-new"), replaces="op-old"))
    assert status == SAVE_UNAVAILABLE
    store.fail_writes = False
    entries, state = asyncio.run(store.load_all_result("s1", owner_user_id="u1"))
    assert entries == [] and state == PENDING_EMPTY            # 旧条已被本轮消费（墓碑），新条没假存


# ── engine ────────────────────────────────────────────────────────────────

def _engine_with_flaky_store():
    engine, spy, _session = _make_engine()
    store = _FlakyStore()
    engine.session = store
    engine.context.session = store
    return engine, spy, store


def test_a_cancel_whose_delete_fails_does_not_claim_cancelled_but_blocks_the_operation():
    engine, spy, store = _engine_with_flaky_store()
    op = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    store.fail_writes = True                                   # 初次读成功，删除时失败
    final = _run(engine, _req("取消"))[-1]
    assert "已为您取消" not in final["speech"]
    assert "不会执行" in final["speech"]
    assert op in (final.get("closed_operation_ids") or [])     # 墓碑保证它不再执行
    store.fail_writes = False                                  # 后端恢复
    final = _run(engine, _req("确认", is_confirmation=True))[-1]
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))
    assert "没有待确认" in final["speech"]


def test_a_cancel_whose_delete_lands_but_the_reply_is_lost_cannot_be_revived():
    engine, spy, store = _engine_with_flaky_store()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    store.ack_lost = True
    _run(engine, _req("取消"))
    store.ack_lost = False
    _run(engine, _req("确认", is_confirmation=True))
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))


def test_a_confirmed_run_whose_settle_delete_fails_is_not_executed_twice():
    engine, spy, store = _engine_with_flaky_store()
    op = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    store.fail_writes = True
    final = _run(engine, _req("确认", is_confirmation=True, operation_id=op))[-1]
    assert sum(1 for m in spy.metas("nearby.order") if m.get("confirmed") == "true") == 1
    assert op in (final.get("closed_operation_ids") or [])
    store.fail_writes = False
    _run(engine, _req("确认", is_confirmation=True))
    assert sum(1 for m in spy.metas("nearby.order") if m.get("confirmed") == "true") == 1


def test_a_successful_cancel_then_confirm_does_not_revive():
    engine, spy, store = _engine_with_flaky_store()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    final = _run(engine, _req("取消"))[-1]
    assert "已为您取消" in final["speech"]
    final = _run(engine, _req("确认", is_confirmation=True))[-1]
    assert "没有待确认" in final["speech"]
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order"))


# ── 重挂起 / 澄清：关旧开新是一次写 ──────────────────────────────────────────

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from orchestrator.cloud.aggregator import Aggregator  # noqa: E402
from orchestrator.cloud.engine import PlannerEngine  # noqa: E402
from orchestrator.cloud.executor import DagExecutor  # noqa: E402
from orchestrator.cloud.planning import PlanBuilder  # noqa: E402


class _SlotCap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description = intent, slots, "创建提醒"


def _reminder_agent():
    manifest = SimpleNamespace(agent_id="reminder", trust_level="first_party", latency_budget_ms=2000,
                               requires_permissions=[],
                               capabilities=[_SlotCap("reminder.create", ["title", "time_text"])])
    return SimpleNamespace(manifest=manifest, endpoint="stub:50080")


class _AskAgainSpy:
    """一个每次都再追问一次时间的提醒 Agent：续接进来 ⇒ 又 NEED_SLOT ⇒ `_suspend(replaces=旧)`。"""

    def __init__(self):
        self.calls = 0

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls += 1
        return SimpleNamespace(status=2, speech="什么时候提醒你？", follow_up="", actions=[],
                               ui_card=None, data=None, missing_slots=["time_text"])

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return json.dumps({"goal": "创建开会提醒", "steps": [
                {"id": "s1", "capability_ref": "cap_0001", "slots": {"title": "开会"},
                 "depends_on": [], "slot_refs": {}}]})
        return "好的。"

    async def resolve(self, query="", intent="", top_k=1):
        return [_reminder_agent()]

    async def list_agents(self):
        return [_reminder_agent()]


def test_a_failed_re_suspend_neither_revives_the_answered_pending_nor_fakes_a_new_one():
    spy = _AskAgainSpy()
    store = _FlakyStore()
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=store)
    first = _run(engine, _req("提醒我开会"))[-1]
    op1 = first["operation_id"]
    store.fail_writes = True                                   # 续接执行后再挂起那一次写失败
    final = _run(engine, _req("明天"))[-1]
    assert "存不下来" in final["speech"]
    assert not final.get("operation_id")                       # 新条没有假装存下了
    assert op1 in (final.get("closed_operation_ids") or [])    # 旧条被本轮消费、立了墓碑
    raw = store._mem[store._session_key("u1", "sess-1")][0]
    assert [s.operation_id for s in raw] == [op1]              # 写失败：旧条还躺在存储里……
    store.fail_writes = False
    entries, state = asyncio.run(store.load_all_result("sess-1", owner_user_id="u1"))
    assert entries == [] and state == PENDING_EMPTY            # ……但读不出来，不复活


def test_a_clarify_suspend_that_cannot_be_saved_is_not_called_a_privacy_wipe(monkeypatch):
    from . import test_engine_clarify_pending as clarify
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    engine, _spy, _session = clarify._make_engine()
    store = _FlakyStore()
    engine.session = store
    engine.context.session = store
    store.fail_writes = True
    final = clarify._run(engine, clarify._req("华润大厦"))[-1]
    assert "正在清除你的数据" not in final["speech"]
    assert "存不下来" in final["speech"]
    assert not final.get("operation_id")


def test_a_clarify_choice_whose_delete_fails_cannot_be_chosen_again(monkeypatch):
    from . import test_engine_clarify_pending as clarify
    monkeypatch.setenv("CLARIFY_ENABLED", "on")
    engine, spy, _session = clarify._make_engine()
    store = _FlakyStore()
    engine.session = store
    engine.context.session = store
    op = clarify._run(engine, clarify._req("华润大厦"))[-1]["operation_id"]
    store.fail_writes = True
    first = clarify._run(engine, clarify._req("第一个"))[-1]
    assert op in (first.get("closed_operation_ids") or [])
    store.fail_writes = False
    assert asyncio.run(store.load("s1", owner_user_id="u1", operation_id=op)) is None


def test_the_re_suspend_replaces_the_answered_pending_in_one_write():
    """行为上「先删旧再存新」与一次整表写在全失败时等价（旧条都读不出来），区别在中间态：两次写之间表里一条都没有、
    第二次失败就是两头落空。这里直接钉「一次写」。"""
    spy = _AskAgainSpy()
    store = _FlakyStore()
    engine = PlannerEngine(clients=spy, planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
                           executor=DagExecutor(call_agent_fn=spy.call_agent),
                           aggregator=Aggregator(llm_fn=spy.llm), session=store)
    _run(engine, _req("提醒我开会"))
    store.writes = 0
    final = _run(engine, _req("明天"))[-1]
    assert final.get("operation_id")
    assert store.writes == 1
    raw = store._mem[store._session_key("u1", "sess-1")][0]
    assert [s.operation_id for s in raw] == [final["operation_id"]]
