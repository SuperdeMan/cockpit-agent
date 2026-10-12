"""轮次的原始发生时刻（CA2-11 v2 §2.8，`AppendTurnRequest.occurred_at_ms`）。

端侧发件箱在重连之后才补写离线那几轮；按到达时刻盖章，「几点做的」就会答成重连那一刻。写入方声明的时刻落在
[服务端时刻 − 会话保留期, 服务端时刻 + 容许偏差] 之内才用，越界（钟走错、超出保留期）按服务端时刻；不声明（0）
逐字同旧。它不进幂等比对。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import store as store_module  # noqa: E402
from cockpit.memory.v1 import memory_pb2  # noqa: E402
from store import MemoryStore, _turn_ts, _SESSION_TTL_S, _OCCURRED_SKEW_S  # noqa: E402

NOW = 1_800_000_000.0


def test_a_declared_time_inside_the_window_is_kept_and_anything_else_is_the_server_time():
    ms = lambda seconds: int(seconds * 1000)  # noqa: E731
    assert _turn_ts(ms(NOW - 600), NOW) == int(NOW - 600)                    # 十分钟前离线发生的
    assert _turn_ts(ms(NOW - _SESSION_TTL_S + 1), NOW) == int(NOW - _SESSION_TTL_S + 1)
    assert _turn_ts(ms(NOW + _OCCURRED_SKEW_S - 1), NOW) == int(NOW + _OCCURRED_SKEW_S - 1)
    for bad in (0, -5, ms(NOW - _SESSION_TTL_S - 10), ms(NOW + _OCCURRED_SKEW_S + 10), True, "123"):
        assert _turn_ts(bad, NOW) == int(NOW), bad


def _store(monkeypatch, now=NOW):
    monkeypatch.setattr(store_module, "time", type("T", (), {"time": staticmethod(lambda: now)}))
    s = MemoryStore()
    s.url = ""
    return s


def test_the_store_stamps_the_declared_time_and_a_replay_with_another_time_is_still_the_same_turn(monkeypatch):
    s = _store(monkeypatch)

    async def go():
        await s.append_turn("s1", "user", "打开车窗", user_id="u1", turn_id="x:user", exchange_id="x",
                            occurred_at_ms=int((NOW - 600) * 1000))
        # 同一轮又补写一次（回包丢了），带的时刻不同：仍是幂等重放，不算改写
        await s.append_turn("s1", "user", "打开车窗", user_id="u1", turn_id="x:user", exchange_id="x",
                            occurred_at_ms=int((NOW - 300) * 1000))
        await s.append_turn("s1", "user", "关闭车窗", user_id="u1", turn_id="y:user", exchange_id="y")
        return await s.get_session("s1", 10, user_id="u1")

    turns = asyncio.run(go())
    assert [(t["text"], t["ts"]) for t in turns] == [("打开车窗", int(NOW - 600)), ("关闭车窗", int(NOW))]


def test_the_field_survives_the_wire_and_an_old_client_reads_as_not_declared():
    declared = memory_pb2.AppendTurnRequest(session_id="s1", turn_id="a:user", occurred_at_ms=1_800_000_000_123)
    assert memory_pb2.AppendTurnRequest.FromString(declared.SerializeToString()).occurred_at_ms == 1_800_000_000_123
    old = memory_pb2.AppendTurnRequest(session_id="s1", turn_id="a:user", memory_epoch="0.x")   # 旧调用方不带这一维
    assert memory_pb2.AppendTurnRequest.FromString(old.SerializeToString()).occurred_at_ms == 0


def test_the_rpc_carries_the_declared_time_through(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "memory_server_occurred_at_under_test", os.path.join(os.path.dirname(__file__), "..", "server.py"))
    mem_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mem_server)
    monkeypatch.setattr(store_module, "time", type("T", (), {"time": staticmethod(lambda: NOW)}))
    svc = mem_server.MemoryServicer()
    svc.store.url = ""
    svc.store._vstore._dsn = ""

    async def go():
        for turn_id, occurred in (("a:user", int((NOW - 120) * 1000)), ("b:user", 0)):
            resp = await svc.AppendTurn(memory_pb2.AppendTurnRequest(
                session_id="s1", role="user", text=turn_id, user_id="u1", occupant_id="primary",
                turn_id=turn_id, exchange_id=turn_id.split(":")[0], occurred_at_ms=occurred), None)
            assert resp.ok
        resp = await svc.GetSession(memory_pb2.GetSessionRequest(
            session_id="s1", last_n=10, user_id="u1", occupant_id="primary"), None)
        return [(t.text, t.ts) for t in resp.turns]

    assert asyncio.run(go()) == [("a:user", int(NOW - 120)), ("b:user", int(NOW))]
