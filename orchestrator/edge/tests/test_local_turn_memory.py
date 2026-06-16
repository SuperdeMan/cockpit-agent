"""端侧本地轮 best-effort 写共享记忆的回归（P1-12）。

纯本地快意图处理完一轮后，端侧应把这轮 best-effort 写进共享记忆，让云端跟进指代消解
（"再低一点"）拿得到上下文；`memory_enabled=false` 时不写；记忆服务不可用时静默跳过、
不阻塞快路径。全部进程内 stub，不连真实 gRPC。
"""
import asyncio
import types

from server import EdgeOrchestratorServicer, _MemoryClient


def _request(session_id="s1", meta=None):
    # _record_local_turn 只读 request.session_id 与 request.meta
    return types.SimpleNamespace(session_id=session_id, meta=meta or {})


def _service(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    return EdgeOrchestratorServicer()


def test_local_turn_writes_user_and_assistant(monkeypatch):
    service = _service(monkeypatch)
    calls = []

    async def fake_append(session_id, role, text):
        calls.append((session_id, role, text))

    service.memory.append = fake_append

    async def run():
        service._record_local_turn(_request(), "空调调到24度", "已设为24度")
        await asyncio.gather(*service._bg)

    asyncio.run(run())

    assert calls == [
        ("s1", "user", "空调调到24度"),
        ("s1", "assistant", "已设为24度"),
    ]


def test_local_turn_skips_when_memory_disabled(monkeypatch):
    service = _service(monkeypatch)
    calls = []

    async def fake_append(*args):
        calls.append(args)

    service.memory.append = fake_append

    async def run():
        service._record_local_turn(
            _request(meta={"memory_enabled": "false"}),
            "空调调到24度",
            "已设为24度",
        )
        await asyncio.gather(*service._bg)

    asyncio.run(run())

    assert calls == []
    assert not service._bg  # 关闭记忆时连后台任务都不该创建


def test_local_turn_skips_without_user_text(monkeypatch):
    service = _service(monkeypatch)
    calls = []

    async def fake_append(*args):
        calls.append(args)

    service.memory.append = fake_append

    async def run():
        service._record_local_turn(_request(), "", "已设为24度")
        await asyncio.gather(*service._bg)

    asyncio.run(run())

    assert calls == []


def test_memory_client_append_swallows_backend_errors():
    # 记忆服务不可用：append 必须静默吞掉异常，不阻塞、不破坏端侧快路径。
    client = _MemoryClient()

    class _BadStub:
        async def AppendTurn(self, *args, **kwargs):
            raise RuntimeError("memory backend down")

    client._stub = lambda: _BadStub()

    async def run():
        await client.append("s1", "user", "hi")  # 不抛即通过

    asyncio.run(run())
