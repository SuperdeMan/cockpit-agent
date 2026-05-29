"""SessionStore 测试。"""
import pytest
import asyncio
from orchestrator.cloud.session import SessionStore
from orchestrator.cloud.models import SessionState


def test_save_and_load():
    store = SessionStore()  # 内存模式
    state = SessionState(phase="wait_confirm", pending_step_id="s2",
                         pending_plan={"steps": []})
    asyncio.run(store.save("sess1", state))
    loaded = asyncio.run(store.load("sess1"))
    assert loaded is not None
    assert loaded.phase == "wait_confirm"
    assert loaded.pending_step_id == "s2"


def test_clear():
    store = SessionStore()
    state = SessionState(phase="wait_slot")
    asyncio.run(store.save("sess2", state))
    asyncio.run(store.clear("sess2"))
    loaded = asyncio.run(store.load("sess2"))
    assert loaded is None


def test_load_nonexistent():
    store = SessionStore()
    loaded = asyncio.run(store.load("no-such-session"))
    assert loaded is None


def test_ttl_expiry():
    """TTL 过期后应返回 None。"""
    store = SessionStore()
    state = SessionState(phase="wait_confirm", ttl_seconds=1)  # 1秒后过期
    asyncio.run(store.save("sess3", state))
    import time
    time.sleep(1.1)  # 等待超过 TTL
    loaded = asyncio.run(store.load("sess3"))
    assert loaded is None
