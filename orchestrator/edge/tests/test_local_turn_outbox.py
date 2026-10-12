"""端侧本地轮什么时候进发件箱（CA2-11 v2 §2.8）。

只有「没送到」才入箱，且只带观测到的代际：现取的，取不到就用请求到达那一刻缓存里的；两者都没有、记忆关闭、
记忆服务明确拒收（删过记忆 / 同 id 异内容）、送到了——都不入箱。同一会话还有待补写的轮时，新的轮排进箱里
而不直写，先后由重放保住。全部进程内替身，不连真实 gRPC。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server as edge_server  # noqa: E402
from server import EdgeOrchestratorServicer  # noqa: E402
from orchestrator.edge.turn_outbox import APPENDED, REFUSED, UNAVAILABLE  # noqa: E402


def _request(session_id="s1", meta=None, *, user_id="u1", request_id="req-1"):
    return types.SimpleNamespace(
        session_id=session_id, meta=meta or {}, request_id=request_id,
        context=types.SimpleNamespace(user_id=user_id, vehicle_id="v1"))


def _service(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    monkeypatch.delenv("EDGE_TURN_OUTBOX", raising=False)
    monkeypatch.delenv("EDGE_OPERATION_LOG", raising=False)
    return EdgeOrchestratorServicer()


class Memory:
    """端侧记忆客户端的替身：代际按 `epochs` 给（缺省取不到），写入按 `outcome` 给结局。"""

    def __init__(self, service, *, epoch="", outcome=APPENDED):
        self.epoch_value = epoch
        self.outcome = outcome
        self.writes = []
        service.memory.epoch = self.epoch
        service.memory.append = self.append

    async def epoch(self, user_id):
        return self.epoch_value

    async def append(self, session_id, role, text, **kw):
        self.writes.append((session_id, role, text, kw))
        return self.outcome(role) if callable(self.outcome) else self.outcome


def _local_turn(service, request, user_text="打开车窗", speech="好的", actions=None):
    async def run():
        observed = service._observe_memory_epoch(request)
        service._record_local_turn(request, user_text, speech, actions=actions, memory_epoch=observed)
        await asyncio.gather(*service._bg)

    asyncio.run(run())


def _boxed(service):
    return [[(t.turn_id, t.memory_epoch) for t in exchange] for exchange in service.turn_outbox.exchanges()]


def test_an_undelivered_turn_is_kept_with_the_epoch_observed_on_arrival(monkeypatch):
    service = _service(monkeypatch)
    Memory(service, epoch="3.ab", outcome=UNAVAILABLE)
    _local_turn(service, _request(request_id="req-a"))
    assert _boxed(service) == [[("req-a:user", "3.ab"), ("req-a:assistant:0", "3.ab")]]


def test_offline_the_turn_takes_the_epoch_cached_when_the_request_arrived(monkeypatch):
    service = _service(monkeypatch)
    Memory(service, epoch="", outcome=UNAVAILABLE)          # 离线：现取不到
    service.memory._epochs["u1"] = "1.old"
    request = _request(request_id="req-b")

    async def run():
        observed = service._observe_memory_epoch(request)   # 到达：此刻缓存的是 1.old
        service.memory._epochs["u1"] = "2.new"               # 之后别的请求才取到的值不算
        service._record_local_turn(request, "打开车窗", "好的", memory_epoch=observed)
        await asyncio.gather(*service._bg)

    asyncio.run(run())
    assert _boxed(service) == [[("req-b:user", "1.old"), ("req-b:assistant:0", "1.old")]]


def test_without_any_observed_epoch_the_turn_is_not_kept(monkeypatch):
    service = _service(monkeypatch)
    memory = Memory(service, epoch="", outcome=UNAVAILABLE)
    _local_turn(service, _request())
    assert len(memory.writes) == 2 and service.turn_outbox.pending() == 0


def test_memory_off_keeps_nothing_and_writes_nothing(monkeypatch):
    service = _service(monkeypatch)
    memory = Memory(service, epoch="3.ab", outcome=UNAVAILABLE)
    _local_turn(service, _request(meta={"memory_enabled": "false"}))
    assert memory.writes == [] and service.turn_outbox.pending() == 0


def test_a_refused_turn_is_not_kept(monkeypatch):
    # 记忆服务拒收（删过记忆 / 同 id 异内容）：重试也不会成；哪怕另一行没送到也不入箱
    service = _service(monkeypatch)
    Memory(service, epoch="3.ab", outcome=lambda role: REFUSED if role == "user" else UNAVAILABLE)
    _local_turn(service, _request())
    assert service.turn_outbox.pending() == 0


def test_a_delivered_turn_is_not_kept(monkeypatch):
    service = _service(monkeypatch)
    Memory(service, epoch="3.ab", outcome=APPENDED)
    _local_turn(service, _request())
    assert service.turn_outbox.pending() == 0


def test_a_half_delivered_turn_is_kept_whole(monkeypatch):
    # user 行送到了、assistant 行没送到：整轮入箱（重放时已送到的那行按 turn_id 幂等）
    service = _service(monkeypatch)
    Memory(service, epoch="3.ab", outcome=lambda role: APPENDED if role == "user" else UNAVAILABLE)
    _local_turn(service, _request(request_id="req-h"))
    assert _boxed(service) == [[("req-h:user", "3.ab"), ("req-h:assistant:0", "3.ab")]]


def test_a_new_turn_queues_behind_the_ones_still_waiting(monkeypatch):
    service = _service(monkeypatch)
    memory = Memory(service, epoch="3.ab", outcome=UNAVAILABLE)
    _local_turn(service, _request(request_id="req-1"), user_text="打开车窗")
    memory.outcome = APPENDED                                # 记忆服务回来了
    memory.writes.clear()
    _local_turn(service, _request(request_id="req-2"), user_text="关闭车窗")
    assert memory.writes == []                               # 没直写：排在 req-1 后面
    assert [[t for t, _ in exchange] for exchange in _boxed(service)] == [
        ["req-1:user", "req-1:assistant:0"], ["req-2:user", "req-2:assistant:0"]]
    assert service.turn_outbox_replayer._kicked              # 已叫醒重放

    asyncio.run(service.turn_outbox_replayer.replay_once())
    assert [(w[1], w[2]) for w in memory.writes] == [
        ("user", "打开车窗"), ("assistant", "好的"), ("user", "关闭车窗"), ("assistant", "好的")]
    assert service.turn_outbox.pending() == 0


def test_replay_brings_back_the_original_ids_epoch_owner_actions_and_time(monkeypatch):
    from runtime import voice_attestation as va

    service = _service(monkeypatch)
    memory = Memory(service, epoch="3.ab", outcome=UNAVAILABLE)
    _local_turn(service, _request(meta={va.IDENTITY: va.UNRECOGNIZED}, request_id="req-r"),
                actions=[{"type": "window.open", "payload": {"command": "window.open"}}])
    [[user, assistant]] = service.turn_outbox.exchanges()
    memory.outcome = APPENDED
    memory.writes.clear()
    asyncio.run(service.turn_outbox_replayer.replay_once())
    [(_, _, _, u), (_, _, _, a)] = memory.writes
    assert (u["turn_id"], a["turn_id"]) == ("req-r:user", "req-r:assistant:0")
    assert u["exchange_id"] == a["exchange_id"] == "req-r"
    assert u["memory_epoch"] == a["memory_epoch"] == "3.ab"
    assert u["speaker_unverified"] and a["speaker_unverified"]
    assert u["occupant_id"] == "primary" and a["actions"] == ["window.open"] and u["actions"] == []
    assert u["occurred_at_ms"] == a["occurred_at_ms"] == user.created_ms == assistant.created_ms > 0


def test_a_fresh_epoch_wakes_the_replay_only_when_something_waits(monkeypatch):
    service = _service(monkeypatch)
    Memory(service, epoch="3.ab", outcome=UNAVAILABLE)

    async def arrive():
        await service._observe_memory_epoch(_request())

    asyncio.run(arrive())
    assert not service.turn_outbox_replayer._kicked          # 箱子空：不叫醒
    _local_turn(service, _request(request_id="req-k"))
    service.turn_outbox_replayer._kicked = False
    asyncio.run(arrive())
    assert service.turn_outbox_replayer._kicked


def test_the_client_reports_what_happened_to_a_write(monkeypatch):
    client = edge_server._MemoryClient()

    class Stub:
        def __init__(self, behaviour):
            self.behaviour = behaviour

        async def AppendTurn(self, request, timeout=None):
            if isinstance(self.behaviour, Exception):
                raise self.behaviour
            return types.SimpleNamespace(ok=self.behaviour, error="" if self.behaviour else "stale_memory_epoch")

        async def GetMemoryEpoch(self, request, timeout=None):
            if isinstance(self.behaviour, Exception):
                raise self.behaviour
            return types.SimpleNamespace(memory_epoch="7.cd")

    async def run(behaviour, **kw):
        client._stub = lambda: Stub(behaviour)
        return await client.append("s1", "user", "hi", user_id="u1", **kw)

    assert asyncio.run(run(True)) == APPENDED
    assert asyncio.run(run(False)) == REFUSED
    assert asyncio.run(run(RuntimeError("down"))) == UNAVAILABLE

    async def epoch(behaviour):
        client._stub = lambda: Stub(behaviour)
        return await client.epoch("u1")

    assert asyncio.run(epoch(RuntimeError("down"))) == "" and client.cached_epoch("u1") == ""
    assert asyncio.run(epoch(True)) == "7.cd" and client.cached_epoch("u1") == "7.cd"
    assert asyncio.run(epoch(RuntimeError("down"))) == "" and client.cached_epoch("u1") == "7.cd"
