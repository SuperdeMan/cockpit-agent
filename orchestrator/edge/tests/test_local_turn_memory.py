"""端侧本地轮 best-effort 写共享记忆的回归（P1-12）。

纯本地快意图处理完一轮后，端侧应把这轮 best-effort 写进共享记忆，让云端跟进指代消解
（"再低一点"）拿得到上下文；`memory_enabled=false` 时不写；记忆服务不可用时静默跳过、
不阻塞快路径。全部进程内 stub，不连真实 gRPC。
"""
import asyncio
import types

import server as edge_server
from server import EdgeOrchestratorServicer, _MemoryClient


def _request(session_id="s1", meta=None, *, user_id="u1", vehicle_id="v1",
             request_id="req-1"):
    # _record_local_turn 读 session_id / meta / context.{user_id,vehicle_id} / request_id
    return types.SimpleNamespace(
        session_id=session_id, meta=meta or {}, request_id=request_id,
        context=types.SimpleNamespace(user_id=user_id, vehicle_id=vehicle_id))


def _service(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    return EdgeOrchestratorServicer()


def test_local_turn_writes_user_and_assistant(monkeypatch):
    service = _service(monkeypatch)
    calls = []

    async def fake_append(session_id, role, text, **kw):
        calls.append((session_id, role, text, kw))

    service.memory.append = fake_append

    async def run():
        service._record_local_turn(_request(), "空调调到24度", "已设为24度")
        await asyncio.gather(*service._bg)

    asyncio.run(run())

    assert [(c[0], c[1], c[2]) for c in calls] == [
        ("s1", "user", "空调调到24度"),
        ("s1", "assistant", "已设为24度"),
    ]


def test_local_turn_carries_owner_and_one_exchange(monkeypatch):
    """M-B：端侧轮次必须带 OwnerKey，且一次本地请求只构成**一个** exchange。

    此前只传 session/role/text——端侧每一轮都是无主的，云端切 OWNER_ONLY 后会全部
    落进 primary 桶，乘员 B 的本地轮次被记成主驾说的。
    """
    service = _service(monkeypatch)
    calls = []

    async def fake_append(session_id, role, text, **kw):
        calls.append((role, kw))

    service.memory.append = fake_append

    async def run():
        service._record_local_turn(
            _request(meta={"occupant_id": "occ-2"}, request_id="req-9"),
            "空调调到24度", "已设为24度")
        await asyncio.gather(*service._bg)

    asyncio.run(run())

    assert [k["occupant_id"] for _, k in calls] == ["occ-2", "occ-2"]
    assert [k["user_id"] for _, k in calls] == ["u1", "u1"]
    assert [k["vehicle_id"] for _, k in calls] == ["v1", "v1"]
    assert {k["exchange_id"] for _, k in calls} == {"req-9"}
    assert [k["turn_id"] for _, k in calls] == ["req-9:user", "req-9:assistant:0"]


def test_latest_local_exchange_boundary_is_forwarded_once(monkeypatch):
    service = _service(monkeypatch)
    service.memory.append = lambda *_args, **_kwargs: None
    local = _request(request_id="local-battery")

    async def run():
        async def fake_append(*_args, **_kwargs):
            return None

        service.memory.append = fake_append
        service._record_local_turn(local, "电量还有多少", "还有72%", actions=[])
        await asyncio.gather(*service._bg)

    asyncio.run(run())
    first_cloud = _request(request_id="cloud-stock")
    service._attach_previous_local_exchange(first_cloud)
    assert first_cloud.meta["_edge_previous_local_exchange"] == "local-battery"

    second_cloud = _request(request_id="cloud-weather")
    service._attach_previous_local_exchange(second_cloud)
    assert "_edge_previous_local_exchange" not in second_cloud.meta


def test_local_only_session_boundaries_are_capacity_bounded(monkeypatch):
    service = _service(monkeypatch)

    async def fake_append(*_args, **_kwargs):
        return None

    service.memory.append = fake_append

    async def run():
        for index in range(300):
            service._record_local_turn(
                _request(session_id=f"local-only-{index}", request_id=f"req-{index}"),
                "电量还有多少", "还有72%", actions=[])
        await asyncio.gather(*service._bg)

    asyncio.run(run())

    assert len(service._last_local_exchange) <= 256
    latest = _request(session_id="local-only-299", request_id="cloud-latest")
    service._attach_previous_local_exchange(latest)
    assert latest.meta["_edge_previous_local_exchange"] == "req-299"
    oldest = _request(session_id="local-only-0", request_id="cloud-oldest")
    service._attach_previous_local_exchange(oldest)
    assert "_edge_previous_local_exchange" not in oldest.meta


def test_expired_local_exchange_boundary_is_dropped_without_blocking(monkeypatch):
    service = _service(monkeypatch)
    request = _request(session_id="ttl-session", request_id="local-ttl")
    key = service._local_exchange_key(request)
    service._last_local_exchange[key] = ("local-ttl", 10.0, ())
    monkeypatch.setattr(
        edge_server.time, "monotonic",
        lambda: 10.0 + edge_server._LOCAL_EXCHANGE_TTL_S + 1.0,
    )

    cloud = _request(session_id="ttl-session", request_id="cloud-after-ttl")
    service._attach_previous_local_exchange(cloud)

    assert "_edge_previous_local_exchange" not in cloud.meta
    assert not service._last_local_exchange


def test_local_turn_without_occupant_falls_back_to_primary(monkeypatch):
    service = _service(monkeypatch)
    calls = []

    async def fake_append(session_id, role, text, **kw):
        calls.append(kw["occupant_id"])

    service.memory.append = fake_append

    async def run():
        service._record_local_turn(_request(), "空调调到24度", "已设为24度")
        await asyncio.gather(*service._bg)

    asyncio.run(run())
    assert calls == ["primary", "primary"]


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
