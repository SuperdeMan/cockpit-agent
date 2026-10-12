"""发件箱 × 真记忆服务（CA2-11 v2 §2.8）：离线那几轮重连后补进会话历史，审计念得出来；删过记忆就补不进去。

端侧走真实的 `_MemoryClient`（真 proto 消息），只把 gRPC 桩换成进程内直调 `MemoryServicer`（内存兜底、真代际
栅栏、真幂等键）。离线 = 桩抛连接错误。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import types

_EDGE_DIR = os.path.join(os.path.dirname(__file__), "..")
_MEM_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "memory")
sys.path.insert(0, _EDGE_DIR)
if _MEM_DIR not in sys.path:
    sys.path.append(_MEM_DIR)          # 记忆服务的裸名模块（fence / store …）放在末尾，不遮住端侧同名模块

from cockpit.memory.v1 import memory_pb2  # noqa: E402
from runtime import session_facts  # noqa: E402
from server import EdgeOrchestratorServicer  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "memory_server_outbox_chain_under_test", os.path.join(_MEM_DIR, "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)

UID, SID = "u-outbox", "s-outbox"


class Link:
    """端侧 → 记忆服务的进程内「网络」：断开时每个调用都抛连接错误。"""

    def __init__(self, servicer):
        self.svc = servicer
        self.online = True

    def _check(self):
        if not self.online:
            raise ConnectionError("memory unreachable")

    async def GetMemoryEpoch(self, request, timeout=None):
        self._check()
        return await self.svc.GetMemoryEpoch(request, None)

    async def AppendTurn(self, request, timeout=None):
        self._check()
        return await self.svc.AppendTurn(request, None)


def _setup(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    monkeypatch.delenv("EDGE_TURN_OUTBOX", raising=False)
    monkeypatch.delenv("EDGE_OPERATION_LOG", raising=False)
    svc = _mem_server.MemoryServicer()
    svc.store.url = ""
    svc.store._vstore._dsn = ""
    edge = EdgeOrchestratorServicer()
    link = Link(svc)
    edge.memory._stub = lambda: link
    return svc, edge, link


def _request(request_id):
    return types.SimpleNamespace(session_id=SID, meta={}, request_id=request_id,
                                 context=types.SimpleNamespace(user_id=UID, vehicle_id="v1"))


async def _local_turn(edge, request_id, text, speech, command):
    request = _request(request_id)
    observed = edge._observe_memory_epoch(request)
    edge._record_local_turn(request, text, speech, actions=[{"type": command, "payload": {"command": command}}],
                            memory_epoch=observed)
    await asyncio.gather(*edge._bg)


async def _history(svc):
    resp = await svc.GetSession(memory_pb2.GetSessionRequest(
        session_id=SID, last_n=20, user_id=UID, occupant_id="primary"), None)
    return [{"role": t.role, "text": t.text, "ts": t.ts, "exchange_id": t.exchange_id,
             "actions": list(t.actions)} for t in resp.turns]


def test_offline_turns_reach_the_history_after_reconnect_and_the_audit_reads_them(monkeypatch):
    svc, edge, link = _setup(monkeypatch)
    store_module = sys.modules[type(svc.store).__module__]

    async def go():
        await _local_turn(edge, "req-1", "打开车窗", "车窗已打开", "window.open")     # 在线：直写，顺带缓存代际
        link.online = False
        await _local_turn(edge, "req-2", "打开后排阅读灯", "阅读灯已打开", "reading_light.open")
        offline = await _history(svc)
        [[queued_user, _]] = edge.turn_outbox.exchanges()
        link.online = True
        # 重连在十分钟之后：会话历史里记的仍是当时的时刻，不是重放那一刻
        later = types.SimpleNamespace(time=lambda: queued_user.created_ms / 1000 + 600)
        monkeypatch.setattr(store_module, "time", later)
        await edge.turn_outbox_replayer.replay_once()
        return offline, queued_user, await _history(svc)

    offline, queued_user, history = asyncio.run(go())
    assert [t["text"] for t in offline] == ["打开车窗", "车窗已打开"]          # 离线那一轮当时没写进去
    assert [t["text"] for t in history] == ["打开车窗", "车窗已打开", "打开后排阅读灯", "阅读灯已打开"]
    assert {t["ts"] for t in history[2:]} == {queued_user.created_ms // 1000}
    assert edge.turn_outbox.pending() == 0
    answer = session_facts.audit_answer(history)
    assert "打开车窗" in answer and "打开后排阅读灯" in answer and "2 个操作" in answer


def test_a_turn_from_before_a_memory_delete_is_never_written_after_it(monkeypatch):
    svc, edge, link = _setup(monkeypatch)

    async def go():
        await _local_turn(edge, "req-1", "打开车窗", "车窗已打开", "window.open")
        link.online = False
        await _local_turn(edge, "req-2", "打开后排阅读灯", "阅读灯已打开", "reading_light.open")
        link.online = True
        await svc.ForgetUser(memory_pb2.ForgetUserRequest(user_id=UID), None)       # 用户删了记忆
        result = await edge.turn_outbox_replayer.replay_once()
        return result, await _history(svc)

    result, history = asyncio.run(go())
    assert result["refused"] == 1 and result["sent"] == 0
    assert history == []                     # 删除之前那一轮没有被补进去
    assert edge.turn_outbox.pending() == 0    # 拒收即丢弃，车上不再留着


def test_replaying_twice_writes_once(monkeypatch):
    svc, edge, link = _setup(monkeypatch)

    async def go():
        await _local_turn(edge, "req-1", "打开车窗", "车窗已打开", "window.open")
        link.online = False
        await _local_turn(edge, "req-2", "打开后排阅读灯", "阅读灯已打开", "reading_light.open")
        [exchange] = edge.turn_outbox.exchanges()
        link.online = True
        await edge.turn_outbox_replayer.replay_once()
        edge.turn_outbox.put(exchange)                         # 同一轮又进了一次箱（例如写成功了但回包丢了）
        await edge.turn_outbox_replayer.replay_once()
        return await _history(svc)

    history = asyncio.run(go())
    assert [t["text"] for t in history].count("打开后排阅读灯") == 1
