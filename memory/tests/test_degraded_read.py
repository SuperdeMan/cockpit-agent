"""批 5 W17：读侧三态的服务端一半——`degraded` 自报 + PG 掉线后的带退避重连。

不连 PG / Redis：用 DSN / URL 有值但连不上的形态模拟「配置了持久后端却在用内存兜底」。
"""
import asyncio
import importlib.util
import os
import sys

_MEM_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _MEM_DIR)
from cockpit.memory.v1 import memory_pb2  # noqa: E402
from pg_store import MemoryVectorStore  # noqa: E402
from store import MemoryStore  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "memory_server_degraded_under_test", os.path.join(_MEM_DIR, "server.py"))
_mem_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mem_server)
MemoryServicer = _mem_server.MemoryServicer


def _servicer(*, dsn: str = "", redis_url: str = "") -> MemoryServicer:
    svc = MemoryServicer()
    svc.store.url = redis_url
    svc.store._vstore._dsn = dsn
    return svc


# ── 向量存储：degraded 的定义 ─────────────────────────────────────────────

def test_no_dsn_is_designed_in_memory_not_degraded():
    """没配 DSN 的栈（本地 / mock）内存就是设计的后端，不是退化。"""
    vs = MemoryVectorStore("")
    assert asyncio.run(vs.init()) is False
    assert vs.degraded is False


def test_dsn_configured_but_pg_down_is_degraded():
    vs = MemoryVectorStore("postgresql://nobody@127.0.0.1:1/none")

    async def go():
        vs._connect = _refuse            # 不真的去连
        await vs.init()
        return vs.degraded

    assert asyncio.run(go()) is True


async def _refuse(*_a, **_k):
    raise ConnectionRefusedError("pg down")


def test_pg_reinit_is_retried_with_backoff(monkeypatch):
    """此前 `init()` 只跑一次：PG 起晚一步，memory 服务就**永远**在用空内存直到重启。
    现在 `ensure()` 在退化态按退避重试；退避窗口内不重试。"""
    vs = MemoryVectorStore("postgresql://nobody@127.0.0.1:1/none")
    attempts = []

    async def fake_init():
        attempts.append(1)
        return False

    vs.init = fake_init
    now = [1000.0]
    monkeypatch.setattr(vs, "_clock", lambda: now[0])

    async def go():
        vs._pg_ok = False
        await vs.ensure()                 # 首次：立刻试一次
        await vs.ensure()                 # 退避窗口内：不试
        now[0] += vs.REINIT_BACKOFF_S + 1
        await vs.ensure()                 # 窗口过了：再试
        for task in list(vs._reinit_tasks):
            await task

    asyncio.run(go())
    assert len(attempts) == 2


def test_ensure_is_noop_when_pg_ok_or_unconfigured():
    vs = MemoryVectorStore("")
    calls = []

    async def fake_init():
        calls.append(1)
        return True

    vs.init = fake_init
    asyncio.run(vs.ensure())
    assert calls == []
    vs._dsn = "postgresql://x"
    vs._pg_ok = True
    asyncio.run(vs.ensure())
    assert calls == []


# ── 服务端自报 ──────────────────────────────────────────────────────────────

def test_recall_reports_degraded_when_pg_configured_but_down():
    svc = _servicer(dsn="postgresql://nobody@127.0.0.1:1/none")
    svc.store._vstore._connect = _refuse

    async def go():
        return await svc.Recall(memory_pb2.RecallRequest(user_id="u1", query="辣"), None)

    resp = asyncio.run(go())
    assert resp.degraded is True
    assert len(resp.items) == 0


def test_recall_not_degraded_on_designed_in_memory_stack():
    svc = _servicer()
    resp = asyncio.run(svc.Recall(memory_pb2.RecallRequest(user_id="u1", query="辣"), None))
    assert resp.degraded is False


def test_get_session_reports_degraded_when_redis_configured_but_down():
    svc = _servicer(redis_url="redis://127.0.0.1:1/0")

    async def fake_redis():
        svc.store._redis_down = True
        return None

    svc.store._redis = fake_redis

    async def go():
        await svc.AppendTurn(memory_pb2.AppendTurnRequest(
            session_id="s1", role="user", text="hi", user_id="u1"), None)
        return await svc.GetSession(
            memory_pb2.GetSessionRequest(session_id="s1", last_n=6, user_id="u1"), None)

    resp = asyncio.run(go())
    assert resp.degraded is True
    assert [t.text for t in resp.turns] == ["hi"]      # 进程内写的那部分照样读得到


def test_get_session_not_degraded_without_redis_url():
    svc = _servicer()
    resp = asyncio.run(svc.GetSession(
        memory_pb2.GetSessionRequest(session_id="s1", last_n=6, user_id="u1"), None))
    assert resp.degraded is False
