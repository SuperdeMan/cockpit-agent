"""CA2-11 v2：车端操作结局的后台对账、结果补记与告知（`operation_sync` + SessionStore 的对账记录）。

后台只读：只发恢复查询，从不重发写命令；查到定论写回对账记录，执行审计据此回答；只在窗口内、且同步路径对用户说过
「会继续核实」时经主动治理器告知。对账记录与挂起、焦点同一个隐私删除出口。
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from google.protobuf.struct_pb2 import Struct

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2

from orchestrator.cloud import operation_sync as sync
from orchestrator.cloud.session import (OPSYNC_EXISTS, SAVE_FENCED, SAVE_OK, SAVE_UNAVAILABLE,
                                        SessionStore)
from orchestrator.edge.operation_log import EdgeOperationLog
from runtime import operation as op
from runtime import operation_gate

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "edge"))
from edge_call import EdgeCallExecutor  # noqa: E402
from val import VAL  # noqa: E402

OK = agent_pb2.ExecuteResponse.OK
T0 = 1_800_000_000_000


class _Clock:
    def __init__(self, now: int = T0):
        self.now = now

    def __call__(self) -> int:
        return self.now


def _answer(status: str):
    data = Struct()
    data.update({"_operation": {"operation_id": "x", "decision": "query", "status": status}})
    return agent_pb2.ExecuteResponse(status=OK, data=data)


class _Vehicle:
    """按 operation 排好回答；`None` = 车端不应答（超时 / 离线）。"""

    def __init__(self, answers=None):
        self.answers = {k: list(v) for k, v in (answers or {}).items()}
        self.queries: list[tuple[str, str]] = []

    async def query(self, vehicle_id, operation_id, *, step_id, timeout):
        self.queries.append((vehicle_id, operation_id))
        queue = self.answers.get(operation_id) or [None]
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        if status is None:
            raise TimeoutError("vehicle offline")
        return _answer(status)


class _Publisher:
    def __init__(self, result="governed"):
        self.result = result
        self.payloads: list[dict] = []

    async def __call__(self, payload):
        self.payloads.append(payload)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _step(operation_id=None, what="打开后备箱"):
    return SimpleNamespace(id="s1", intent="trunk.open", operation_id=operation_id or op.new_operation_id(),
                           capability_description=what)


def _ctx(session_id="sess-1", user_id="u1", vehicle_id="v1"):
    return SimpleNamespace(session_id=session_id, user_id=user_id, vehicle_id=vehicle_id,
                           occupant_id="primary", request_id="req-1")


def _rig(answers=None, publish=None, clock=None):
    clock = clock or _Clock()
    store = SessionStore(redis_url="")
    vehicle = _Vehicle(answers)
    syncer = sync.EdgeOperationSync(store, vehicle.query, publish=publish, now_ms=clock)
    return clock, store, vehicle, syncer


def _records(store, session_id="sess-1", owner="u1"):
    return asyncio.run(store.load_operations(session_id, owner_user_id=owner))


def _tick(syncer):
    return asyncio.run(syncer.tick())


# ── SessionStore：对账记录的存取与隐私删除 ─────────────────────────────────────────

def _entry(operation_id, status=op.SYNC_CHECKING):
    return {"operation_id": operation_id, "status": status, "created_ms": T0, "what": "打开后备箱"}


def test_a_record_is_owned_and_written_once():
    store = SessionStore(redis_url="")
    oid = op.new_operation_id()
    assert asyncio.run(store.note_operation("s", _entry(oid), owner_user_id="u1", ttl_s=60,
                                            active=True)) == SAVE_OK
    assert asyncio.run(store.note_operation("s", _entry(oid, op.SYNC_DONE), owner_user_id="u1", ttl_s=60,
                                            active=False)) == OPSYNC_EXISTS
    [record] = asyncio.run(store.load_operations("s", owner_user_id="u1"))
    assert record["status"] == op.SYNC_CHECKING and record["owner_user_id"] == "u1"
    assert asyncio.run(store.load_operations("s", owner_user_id="u2")) == []   # 同一个 session_id 也读不到别人的


def test_only_unsettled_records_are_in_the_active_set_and_write_back_never_revives():
    store = SessionStore(redis_url="")
    open_id, done_id = op.new_operation_id(), op.new_operation_id()
    asyncio.run(store.note_operation("s", _entry(open_id), owner_user_id="u1", ttl_s=60, active=True))
    asyncio.run(store.note_operation("s", _entry(done_id, op.SYNC_DONE), owner_user_id="u1", ttl_s=60,
                                     active=False))
    [(member, record)] = asyncio.run(store.active_operations())
    assert record["operation_id"] == open_id
    assert asyncio.run(store.resolve_operation(member, {**record, "status": op.SYNC_DONE}, active=False))
    assert asyncio.run(store.active_operations()) == []
    assert asyncio.run(store.delete_owner("u1")) is True
    assert asyncio.run(store.resolve_operation(member, record, active=True)) is False   # 删掉的不复活
    assert asyncio.run(store.active_operations()) == []


def test_write_back_does_not_recreate_an_expired_or_missing_record():
    """不经写栅栏的那条路：记录过期了，或成员指向的记录根本不在——写回一律不复活，并把悬空成员摘掉。"""
    import time as _time
    store = SessionStore(redis_url="")
    oid = op.new_operation_id()
    asyncio.run(store.note_operation("s", _entry(oid), owner_user_id="u1", ttl_s=60, active=True))
    [(member, record)] = asyncio.run(store.active_operations())
    key = member.rpartition("|")[0]
    table, _expires = store._opsync_mem[key]
    store._opsync_mem[key] = (table, _time.time() - 1)          # 过期
    assert asyncio.run(store.resolve_operation(member, {**record, "status": op.SYNC_DONE}, active=False)) is False
    assert asyncio.run(store.load_operations("s", owner_user_id="u1")) == []
    ghost = store._opsync_member(store._opsync_key("u1", "s"), op.new_operation_id())
    assert asyncio.run(store.resolve_operation(ghost, {**record, "operation_id": "x"}, active=True)) is False
    assert asyncio.run(store.load_operations("s", owner_user_id="u1")) == []
    assert asyncio.run(store.active_operations()) == []


def test_owner_deletion_removes_the_records_and_fences_new_ones():
    store = SessionStore(redis_url="")
    mine, theirs = op.new_operation_id(), op.new_operation_id()
    asyncio.run(store.note_operation("s", _entry(mine), owner_user_id="u1", ttl_s=60, active=True))
    asyncio.run(store.note_operation("s", _entry(theirs), owner_user_id="u2", ttl_s=60, active=True))
    assert asyncio.run(store.delete_owner("u1")) is True
    assert asyncio.run(store.load_operations("s", owner_user_id="u1")) == []
    assert [r["operation_id"] for _m, r in asyncio.run(store.active_operations())] == [theirs]
    assert asyncio.run(store.note_operation("s", _entry(op.new_operation_id()), owner_user_id="u1",
                                            ttl_s=60, active=True)) == SAVE_FENCED
    assert len(asyncio.run(store.load_operations("s", owner_user_id="u2"))) == 1


def test_a_configured_but_unreachable_backend_says_unavailable_not_empty():
    store = SessionStore(redis_url="redis://configured-but-down:6379/0")

    async def down():
        return None

    store._redis = down
    assert asyncio.run(store.note_operation("s", _entry(op.new_operation_id()), owner_user_id="u1",
                                            ttl_s=60, active=True)) == SAVE_UNAVAILABLE
    assert asyncio.run(store.load_operations("s", owner_user_id="u1")) is None
    assert asyncio.run(store.active_operations()) is None


def test_the_records_are_declared_for_privacy_with_the_pending_session_family():
    # 只按属性取：e2e 契约静态扫描全仓，提到这个名字的文件不许再 import 它（否则算成第二份隐私清单）
    from orchestrator.cloud import session as session_module
    from runtime.privacy_registry import PRIVACY_TARGETS
    [target] = [t for t in session_module.PERSONAL_DATA_TARGETS if t["id"] == "planner_pending_session"]
    assert {"planner:opsync:*", "planner:opsync-active"} <= set(target["storage_variants"])
    [spec] = [t for t in PRIVACY_TARGETS if t.id == "planner_pending_session"]
    assert set(spec.storage_variants) == set(target["storage_variants"])


# ── 登记：同步路径收尾时 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("edge_status", ["", op.ACCEPTED, op.EDGE_ABSENT])
def test_an_unsettled_operation_is_handed_to_the_background(edge_status):
    _clock, store, _vehicle, syncer = _rig()
    step = _step()
    assert asyncio.run(syncer.note(step, _ctx(), edge_status)) is True
    [record] = _records(store)
    assert record["status"] == op.SYNC_CHECKING and record["notify"] is True
    assert record["what"] == "打开后备箱" and record["vehicle_id"] == "v1"
    assert record["deadline_ms"] == T0 + sync.HORIZON_S * 1000
    assert [r["operation_id"] for _m, r in asyncio.run(store.active_operations())] == [step.operation_id]


@pytest.mark.parametrize("edge_status, outcome", [
    (op.DONE, op.SYNC_DONE), (op.CANCELLED, op.SYNC_NOT_EXECUTED), (op.ORPHANED, op.SYNC_UNKNOWN)])
def test_an_outcome_known_at_once_is_only_recorded_for_the_audit(edge_status, outcome):
    _clock, store, _vehicle, syncer = _rig()
    assert asyncio.run(syncer.note(_step(), _ctx(), edge_status)) is True
    [record] = _records(store)
    assert record["status"] == outcome and record["via"] == "sync" and record["notify"] is False
    assert asyncio.run(store.active_operations()) == []


@pytest.mark.parametrize("ctx", [_ctx(session_id=""), _ctx(user_id=""), _ctx(vehicle_id="")])
def test_nothing_is_recorded_without_an_owner_a_session_or_a_vehicle(ctx):
    _clock, store, _vehicle, syncer = _rig()
    assert asyncio.run(syncer.note(_step(), ctx, "")) is False
    assert asyncio.run(syncer.note(SimpleNamespace(id="s1", intent="x", operation_id="nope",
                                                   capability_description=""), _ctx(), "")) is False


# ── 后台对账 ─────────────────────────────────────────────────────────────────

def test_the_background_asks_only_when_due_and_settles_what_the_vehicle_says():
    step = _step()
    publish = _Publisher()
    clock, store, vehicle, syncer = _rig({step.operation_id: [op.DONE]}, publish=publish)
    asyncio.run(syncer.note(step, _ctx(), ""))
    assert _tick(syncer) == 0 and vehicle.queries == []              # 还没到第一次再问的时间
    clock.now += sync.BACKOFF_S[0] * 1000
    assert _tick(syncer) == 1
    assert vehicle.queries == [("v1", step.operation_id)]
    [record] = _records(store)
    assert record["status"] == op.SYNC_DONE and record["via"] == "background" and record["notified"] is True
    assert asyncio.run(store.active_operations()) == []
    [payload] = publish.payloads
    assert payload["speech"] == "刚才「打开后备箱」没收到执行回执，车端记录显示已经完成。"
    assert payload["priority"] == "advisory" and payload["ttl_ms"] == sync.NOTIFY_TTL_MS
    assert payload["dedup_key"] == f"operation_outcome|{step.operation_id}"
    assert (payload["user_id"], payload["vehicle_id"]) == ("u1", "v1")     # 只告诉下达命令的人、这辆车


def test_still_running_or_unanswered_keeps_checking_with_growing_intervals():
    step = _step()
    clock, store, vehicle, syncer = _rig({step.operation_id: [op.ACCEPTED, None, op.ACCEPTED]})
    asyncio.run(syncer.note(step, _ctx(), ""))
    seen = []
    for _ in range(3):
        [(_m, record)] = asyncio.run(store.active_operations())
        clock.now = record["next_ms"]
        _tick(syncer)
        [(_m, record)] = asyncio.run(store.active_operations())
        seen.append((record["attempts"], record["next_ms"] - clock.now, record["edge_status"]))
    assert seen == [(1, sync.BACKOFF_S[0] * 1000, op.ACCEPTED),
                    (2, sync.BACKOFF_S[1] * 1000, "unanswered"),
                    (3, sync.BACKOFF_S[2] * 1000, op.ACCEPTED)]
    assert len(vehicle.queries) == 3


def test_not_on_record_right_after_the_resend_is_not_yet_a_verdict():
    """同步路径刚重发过同一份：查无此条可能是它还在路上。宽限期之后才算「没有执行」。"""
    step = _step()
    clock, store, _vehicle, syncer = _rig({step.operation_id: [op.EDGE_ABSENT]})
    asyncio.run(syncer.note(step, _ctx(), op.EDGE_ABSENT))
    clock.now = T0 + sync.BACKOFF_S[0] * 1000
    _tick(syncer)
    assert _records(store)[0]["status"] == op.SYNC_CHECKING
    clock.now = T0 + sync.ABSENT_GRACE_S * 1000
    _tick(syncer)
    assert _records(store)[0]["status"] == op.SYNC_NOT_EXECUTED


def test_past_the_horizon_it_settles_as_unknown_without_asking():
    step = _step()
    clock, store, vehicle, syncer = _rig({step.operation_id: [op.DONE]}, publish=_Publisher())
    asyncio.run(syncer.note(step, _ctx(), ""))
    clock.now = T0 + sync.HORIZON_S * 1000
    _tick(syncer)
    [record] = _records(store)
    assert record["status"] == op.SYNC_UNKNOWN and record["edge_status"] == "deadline"
    assert vehicle.queries == [] and asyncio.run(store.active_operations()) == []


def test_a_late_verdict_is_recorded_but_not_announced():
    step = _step()
    publish = _Publisher()
    clock, store, _vehicle, syncer = _rig({step.operation_id: [op.FAILED]}, publish=publish)
    asyncio.run(syncer.note(step, _ctx(), ""))
    clock.now = T0 + sync.NOTIFY_WINDOW_S * 1000 + 1
    _tick(syncer)
    [record] = _records(store)
    assert record["status"] == op.SYNC_FAILED and record["notified"] is False
    assert publish.payloads == []


@pytest.mark.parametrize("result", ["skipped", RuntimeError("nats down")])
def test_an_announcement_that_did_not_go_out_is_not_marked_as_sent(result):
    step = _step()
    clock, store, _vehicle, syncer = _rig({step.operation_id: [op.ORPHANED]}, publish=_Publisher(result))
    asyncio.run(syncer.note(step, _ctx(), ""))
    clock.now += sync.BACKOFF_S[0] * 1000
    _tick(syncer)
    [record] = _records(store)
    assert record["status"] == op.SYNC_UNKNOWN and record["notified"] is False


def test_unknown_is_announced_with_a_reminder_to_check_the_car():
    assert sync.outcome_notice({"what": "打开后备箱", "status": op.SYNC_UNKNOWN}) == (
        "刚才「打开后备箱」没收到执行回执，车端记录也确认不了做没做成。请留意车辆状态。")
    assert sync.outcome_notice({"what": "打开后备箱", "status": "weird"}) == ""


def test_an_owner_deleted_while_checking_is_neither_asked_about_nor_announced():
    step = _step()
    publish = _Publisher()
    clock, store, vehicle, syncer = _rig({step.operation_id: [op.DONE]}, publish=publish)
    asyncio.run(syncer.note(step, _ctx(), ""))
    assert asyncio.run(store.delete_owner("u1")) is True
    clock.now += sync.BACKOFF_S[0] * 1000
    assert _tick(syncer) == 0
    assert vehicle.queries == [] and publish.payloads == []


def test_the_background_only_reads_the_vehicle_log_and_never_runs_a_command():
    """端到端：真车端执行器 + 日志。后台一路问到定论，VAL 一条命令都没跑过。"""
    edge_clock = _Clock(1_000_000)
    val = VAL()
    commands = []
    real = val.execute

    def counting(*args, **kwargs):
        commands.append(args[0])
        return real(*args, **kwargs)

    val.execute = counting
    log = EdgeOperationLog(":memory:", clock_ms=edge_clock)
    edge = EdgeCallExecutor(val, operation_log=log)
    step = _step()
    assert asyncio.run(log.operation_insert(operation_id=step.operation_id, user_id="vehicle:v1",
                                            session_id="", agent_id="edge-vehicle", trace_id="",
                                            envelope={"phase": op.PHASE_EXECUTING, "binding_sha256": "b"}))
    calls = []

    async def query(vehicle_id, operation_id, *, step_id, timeout):
        calls.append(operation_id)
        return await edge.dispatch(channel_pb2.EdgeCall(step_id=step_id, operation_query=operation_id))

    clock = _Clock()
    store = SessionStore(redis_url="")
    syncer = sync.EdgeOperationSync(store, query, now_ms=clock)
    asyncio.run(syncer.note(step, _ctx(), op.ACCEPTED))
    clock.now += sync.BACKOFF_S[0] * 1000
    _tick(syncer)
    assert _records(store)[0]["status"] == op.SYNC_CHECKING          # 车端还在执行中
    edge_clock.now += int(operation_gate.executing_stale_s() * 1000)
    clock.now += sync.BACKOFF_S[1] * 1000
    _tick(syncer)
    assert _records(store)[0]["status"] == op.SYNC_UNKNOWN           # 执行方中途死掉：确认不了，不重放
    assert commands == [] and calls == [step.operation_id] * 2


def test_the_response_reader_takes_only_what_it_recognises():
    assert sync.edge_operation_status(_answer(op.DONE)) == op.DONE
    assert sync.edge_operation_status(agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.REJECTED)) == ""
    assert sync.edge_operation_status(agent_pb2.ExecuteResponse(status=OK)) == ""
    assert sync.edge_operation_status(None) == ""


def test_audit_items_keep_only_known_outcomes_in_order():
    records = [{"what": "打开后备箱", "status": op.SYNC_DONE}, {"what": "x", "status": "?"}, "bad",
               {"what": "关闭天窗", "status": op.SYNC_CHECKING}]
    assert sync.audit_items(records) == [{"what": "打开后备箱", "outcome": op.SYNC_DONE},
                                         {"what": "关闭天窗", "outcome": op.SYNC_CHECKING}]
