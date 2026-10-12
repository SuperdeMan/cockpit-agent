"""车端会话轮发件箱（CA2-11 v2 §2.8）：存储与重放循环。

只测发件箱自己：入箱的门槛、先后、保留期、容量、进程重启后还在；重放按先后送、收下 / 拒收都删、没送到记退避且
同一会话后面的轮不越过它。端侧服务里「什么时候入箱」见 `test_local_turn_outbox.py`，与真记忆服务（代际栅栏）
连起来的链路见 `test_turn_outbox_memory_chain.py`。
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from orchestrator.edge.turn_outbox import (APPENDED, REFUSED, UNAVAILABLE, EdgeTurnOutbox, OutboxTurn,
                                           TurnOutboxReplayer, RETENTION_MS, SIBLING_FILE)


class Clock:
    def __init__(self, now=1_800_000_000_000):
        self.now = now

    def __call__(self):
        return self.now


def _exchange(exch, *, session="s1", user="u1", epoch="0.e", created=1_800_000_000_000, speech="好的",
              actions=("window.open",), unverified=False, occupant="primary"):
    turns = [OutboxTurn(turn_id=f"{exch}:user", exchange_id=exch, seq=0, session_id=session, user_id=user,
                        occupant_id=occupant, vehicle_id="v1", role="user", text=f"说的话 {exch}", actions=(),
                        memory_epoch=epoch, speaker_unverified=unverified, created_ms=created)]
    if speech:
        turns.append(OutboxTurn(turn_id=f"{exch}:assistant:0", exchange_id=exch, seq=1, session_id=session,
                                user_id=user, occupant_id=occupant, vehicle_id="v1", role="assistant",
                                text=speech, actions=tuple(actions), memory_epoch=epoch,
                                speaker_unverified=unverified, created_ms=created))
    return turns


def _box(clock=None, path=":memory:", **kw):
    return EdgeTurnOutbox(path, clock_ms=clock or Clock(), **kw)


def _ids(box):
    return [[t.turn_id for t in exchange] for exchange in box.exchanges()]


def test_an_exchange_comes_back_user_first_and_exchanges_in_the_order_they_happened():
    clock = Clock()
    box = _box(clock)
    assert box.put(list(reversed(_exchange("b", created=clock.now + 5))))
    assert box.put(_exchange("a", created=clock.now))
    assert _ids(box) == [["a:user", "a:assistant:0"], ["b:user", "b:assistant:0"]]
    first = box.exchanges()[0]
    assert first[1].actions == ("window.open",) and first[0].actions == ()
    assert first[0].memory_epoch == "0.e" and first[0].created_ms == clock.now


def test_nothing_without_an_observed_epoch_or_an_owner_is_kept():
    box = _box()
    assert not box.put(_exchange("a", epoch=""))
    assert not box.put(_exchange("b", user=""))
    assert not box.put([])
    assert box.pending() == 0


def test_the_table_itself_refuses_an_empty_epoch():
    # 第二道：绕过 put 直接写也写不进空代际——空代际在记忆服务那边等于不过栅栏
    box = _box()
    with pytest.raises(sqlite3.IntegrityError):
        box._db.execute(
            "INSERT INTO edge_turn_outbox (turn_id, exchange_id, seq, session_id, user_id, role, text,"
            " memory_epoch, created_ms, next_ms) VALUES ('x:user','x',0,'s','u','user','t','',1,1)")


def test_putting_the_same_exchange_twice_keeps_one_copy():
    box = _box()
    assert box.put(_exchange("a")) and box.put(_exchange("a"))
    assert box.pending() == 2


def test_rows_past_the_retention_go_away_as_a_whole_exchange():
    clock = Clock()
    box = _box(clock)
    box.put(_exchange("old", created=clock.now))
    box.put(_exchange("new", created=clock.now + 60_000))
    clock.now += RETENTION_MS + 1
    assert box.purge() == 2
    assert _ids(box) == [["new:user", "new:assistant:0"]]


def test_a_full_box_takes_no_new_exchange():
    box = _box(max_rows=3)
    assert box.put(_exchange("a"))
    assert not box.put(_exchange("b"))
    assert _ids(box) == [["a:user", "a:assistant:0"]]


def test_the_box_survives_a_restart(tmp_path):
    path = str(tmp_path / "vehicle" / SIBLING_FILE)
    clock = Clock()
    first = _box(clock, path)
    first.put(_exchange("a", unverified=True, occupant="occ-2"))
    first._db.close()
    again = _box(clock, path)
    [[user, assistant]] = again.exchanges()
    assert (user.turn_id, assistant.turn_id) == ("a:user", "a:assistant:0")
    assert user.speaker_unverified and user.occupant_id == "occ-2" and assistant.actions == ("window.open",)


def test_without_its_own_path_the_box_sits_next_to_the_operation_log(tmp_path):
    log = tmp_path / "data" / "edge-operations.sqlite3"
    box = EdgeTurnOutbox.from_env({"EDGE_OPERATION_LOG": str(log)})
    assert box.path == os.path.join(str(log.parent), SIBLING_FILE) and box.persistent
    assert EdgeTurnOutbox.from_env({"EDGE_TURN_OUTBOX": str(tmp_path / "x.sqlite3")}).path.endswith("x.sqlite3")
    assert EdgeTurnOutbox.from_env({}).path == ":memory:"


# ── 重放 ──────────────────────────────────────────────────────────────


class Memory:
    """记录送了什么；按 turn_id 定结局（缺省收下）。"""

    def __init__(self, outcomes=None):
        self.sent: list[OutboxTurn] = []
        self.outcomes = dict(outcomes or {})

    async def __call__(self, turn):
        self.sent.append(turn)
        outcome = self.outcomes.get(turn.turn_id, APPENDED)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_replay_sends_in_order_and_deletes_what_was_taken():
    clock = Clock()
    box = _box(clock)
    box.put(_exchange("b", created=clock.now + 1))
    box.put(_exchange("a", created=clock.now))
    memory = Memory()
    result = asyncio.run(TurnOutboxReplayer(box, memory).replay_once())
    assert [t.turn_id for t in memory.sent] == ["a:user", "a:assistant:0", "b:user", "b:assistant:0"]
    assert result == {"sent": 2, "refused": 0, "unavailable": False} and box.pending() == 0


def test_a_refused_exchange_is_dropped_without_sending_its_reply():
    box = _box()
    box.put(_exchange("a"))
    memory = Memory({"a:user": REFUSED})
    result = asyncio.run(TurnOutboxReplayer(box, memory).replay_once())
    assert [t.turn_id for t in memory.sent] == ["a:user"]
    assert result["refused"] == 1 and box.pending() == 0


def test_an_unreachable_memory_keeps_the_exchange_with_a_backoff_and_stops_the_pass():
    clock = Clock()
    box = _box(clock)
    box.put(_exchange("a", created=clock.now))
    box.put(_exchange("b", session="s2", created=clock.now + 1))
    memory = Memory({"a:assistant:0": UNAVAILABLE})
    replayer = TurnOutboxReplayer(box, memory, backoff_s=(5, 15))
    assert asyncio.run(replayer.replay_once())["unavailable"]
    assert [t.turn_id for t in memory.sent] == ["a:user", "a:assistant:0"]    # 本次停手，b 没送
    a = box.exchanges()[0]
    assert {t.attempts for t in a} == {1} and {t.next_ms for t in a} == {clock.now + 5_000}
    memory.sent.clear()
    asyncio.run(replayer.replay_once())                                      # 退避未到：a 不送，b 照送
    assert [t.turn_id for t in memory.sent] == ["b:user", "b:assistant:0"]
    clock.now += 5_000
    asyncio.run(replayer.replay_once())                                      # 再失败：退避升一档
    assert {t.attempts for t in box.exchanges()[0]} == {2}
    assert {t.next_ms for t in box.exchanges()[0]} == {clock.now + 15_000}


def test_a_later_exchange_of_the_same_session_never_overtakes_one_in_backoff():
    clock = Clock()
    box = _box(clock)
    box.put(_exchange("a", created=clock.now))
    box.put(_exchange("b", created=clock.now + 1))
    box.defer("a", attempts=1, next_ms=clock.now + 60_000)
    memory = Memory()
    asyncio.run(TurnOutboxReplayer(box, memory).replay_once())
    assert memory.sent == [] and box.pending() == 4


def test_a_kick_voids_the_backoff():
    clock = Clock()
    box = _box(clock)
    box.put(_exchange("a"))
    box.defer("a", attempts=3, next_ms=clock.now + 60_000)
    memory = Memory()
    replayer = TurnOutboxReplayer(box, memory)
    replayer.kick()
    asyncio.run(replayer.replay_once())
    assert [t.turn_id for t in memory.sent] == ["a:user", "a:assistant:0"] and box.pending() == 0


def test_a_kick_on_an_empty_box_does_nothing():
    replayer = TurnOutboxReplayer(_box(), Memory())
    replayer.kick()
    assert not replayer._kicked and not replayer._wake.is_set()


def test_a_client_that_raises_counts_as_not_delivered():
    box = _box()
    box.put(_exchange("a"))
    result = asyncio.run(TurnOutboxReplayer(box, Memory({"a:user": RuntimeError("boom")})).replay_once())
    assert result["unavailable"] and box.pending() == 2


def test_the_loop_survives_a_failing_pass_and_wakes_on_a_kick():
    box = _box()
    replayer = TurnOutboxReplayer(box, Memory(), idle_s=60)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("disk hiccup")
        replayer._kicked = False                         # 同真实的一轮：叫醒标记在一轮开头消费掉
        return {}

    replayer.replay_once = flaky

    async def go():
        task = asyncio.create_task(replayer.run_forever())
        await asyncio.sleep(0.05)
        box.put(_exchange("a"))
        replayer.kick()
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert len(calls) >= 2          # 第一轮抛了没拖垮循环；叫醒后又跑了一轮


def test_a_pass_that_keeps_failing_after_a_kick_does_not_spin():
    box = _box()
    box.put(_exchange("a"))
    replayer = TurnOutboxReplayer(box, Memory(), idle_s=60)
    calls = []

    async def broken():
        calls.append(1)
        raise RuntimeError("disk hiccup")          # 在消费叫醒标记之前就抛了

    replayer.replay_once = broken

    async def go():
        replayer.kick()
        task = asyncio.create_task(replayer.run_forever())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert len(calls) == 1                          # 出错后按延迟等，不是原地重来


def test_the_text_never_reaches_the_log(caplog):
    clock = Clock()
    box = _box(clock, max_rows=2)
    caplog.set_level("DEBUG", logger="edge.turn_outbox")
    box.put(_exchange("a"))
    box.put(_exchange("b"))                              # 满了：告警
    asyncio.run(TurnOutboxReplayer(box, Memory()).replay_once())
    clock.now += RETENTION_MS + 1
    box.put(_exchange("c", created=clock.now - RETENTION_MS - 5))
    box.purge()
    assert caplog.records and not any("说的话" in r.getMessage() or "好的" in r.getMessage()
                                      for r in caplog.records)
