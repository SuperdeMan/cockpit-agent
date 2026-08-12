"""SessionStore 测试。"""
import pytest
import asyncio
import time
from orchestrator.cloud.session import SessionStore
from orchestrator.cloud.models import SessionState


def test_save_and_load():
    store = SessionStore()  # 内存模式
    state = SessionState(phase="wait_confirm", owner_user_id="u1",
                         pending_step_id="s2",
                         pending_plan={"steps": []})
    asyncio.run(store.save("sess1", state))
    loaded = asyncio.run(store.load("sess1", owner_user_id="u1"))
    assert loaded is not None
    assert loaded.phase == "wait_confirm"
    assert loaded.pending_step_id == "s2"


def test_clear():
    store = SessionStore()
    state = SessionState(phase="wait_slot", owner_user_id="u1")
    asyncio.run(store.save("sess2", state))
    asyncio.run(store.clear("sess2", owner_user_id="u1"))
    loaded = asyncio.run(store.load("sess2", owner_user_id="u1"))
    assert loaded is None


def test_load_nonexistent():
    store = SessionStore()
    loaded = asyncio.run(store.load(
        "no-such-session", owner_user_id="u1"))
    assert loaded is None


def test_ttl_expiry():
    """TTL 过期后应返回 None。"""
    store = SessionStore()
    state = SessionState(
        phase="wait_confirm", owner_user_id="u1", ttl_seconds=1)
    asyncio.run(store.save("sess3", state))
    import time
    time.sleep(1.1)  # 等待超过 TTL
    loaded = asyncio.run(store.load("sess3", owner_user_id="u1"))
    assert loaded is None


def test_delete_owner_clears_only_that_users_pending_sessions():
    store = SessionStore(redis_url="")
    asyncio.run(store.save("s-u1-a", SessionState(
        phase="wait_confirm", owner_user_id="u1",
        completed_results={"m": {"data": {"checkout_token": "secret"}}})))
    asyncio.run(store.save("s-u1-b", SessionState(
        phase="wait_slot", owner_user_id="u1")))
    asyncio.run(store.save("s-u2", SessionState(
        phase="wait_confirm", owner_user_id="u2")))

    assert asyncio.run(store.delete_owner("u1")) is True
    assert asyncio.run(store.load(
        "s-u1-a", owner_user_id="u1")) is None
    assert asyncio.run(store.load(
        "s-u1-b", owner_user_id="u1")) is None
    assert asyncio.run(store.load(
        "s-u2", owner_user_id="u2")) is not None


def test_delete_owner_fences_new_pending_saves_for_pending_ttl():
    store = SessionStore(redis_url="")
    assert asyncio.run(store.delete_owner("u1")) is True

    saved = asyncio.run(store.save("late-u1", SessionState(
        phase="wait_confirm", owner_user_id="u1")))
    assert saved is False
    assert asyncio.run(store.load(
        "late-u1", owner_user_id="u1")) is None


def test_memory_delete_tombstone_covers_longest_remaining_record_ttl():
    store = SessionStore(redis_url="")
    assert asyncio.run(store.save("long", SessionState(
        phase="wait_confirm", owner_user_id="u1", ttl_seconds=600))) is True
    record_expires = store._mem[store._session_key("u1", "long")][1]

    assert asyncio.run(store.delete_owner("u1")) is True

    assert store._owner_fences["u1"] >= record_expires
    assert asyncio.run(store.save("late", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is False


def test_delete_owner_also_clears_owner_scoped_focus():
    store = SessionStore(redis_url="")
    asyncio.run(store.save_focus(
        "focus-u1", {"last_poi": "private"}, owner_user_id="u1"))
    asyncio.run(store.save_focus(
        "focus-u2", {"last_poi": "control"}, owner_user_id="u2"))

    assert asyncio.run(store.delete_owner("u1")) is True
    assert asyncio.run(store.load_focus(
        "focus-u1", owner_user_id="u1")) is None
    control = asyncio.run(store.load_focus(
        "focus-u2", owner_user_id="u2"))
    assert control is not None and control["last_poi"] == "control"


def test_delete_owner_fails_closed_when_configured_redis_is_unavailable():
    store = SessionStore(redis_url="redis://configured-but-unavailable:6379/0")

    async def unavailable():
        return None

    store._redis = unavailable
    assert asyncio.run(store.delete_owner("u1")) is False


def test_pending_and_focus_are_bound_to_authenticated_owner():
    store = SessionStore(redis_url="")
    assert asyncio.run(store.save("shared", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is True
    assert asyncio.run(store.save_focus(
        "shared", {"last_poi": "private"}, owner_user_id="u1")) is True

    assert asyncio.run(store.load("shared", owner_user_id="u2")) is None
    assert asyncio.run(store.load_focus(
        "shared", owner_user_id="u2")) is None
    assert asyncio.run(store.clear("shared", owner_user_id="u2")) is False
    assert asyncio.run(store.load(
        "shared", owner_user_id="u1")) is not None


def test_ownerless_pending_and_focus_fail_closed():
    store = SessionStore(redis_url="")

    assert asyncio.run(store.save(
        "legacy", SessionState(phase="wait_confirm"))) is False
    assert asyncio.run(store.save_focus("legacy", {"last_poi": "x"})) is False
    assert asyncio.run(store.load("legacy", owner_user_id="u1")) is None
    assert asyncio.run(store.load_focus(
        "legacy", owner_user_id="u1")) is None


def test_configured_redis_never_loads_or_writes_memory_fallback():
    store = SessionStore(redis_url="")
    assert asyncio.run(store.save("s1", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is True
    assert asyncio.run(store.save_focus(
        "s1", {"last_poi": "private"}, owner_user_id="u1")) is True
    store._url = "redis://configured-but-unavailable:6379/0"

    async def unavailable():
        return None

    store._redis = unavailable
    assert asyncio.run(store.load("s1", owner_user_id="u1")) is None
    assert asyncio.run(store.load_focus("s1", owner_user_id="u1")) is None
    assert asyncio.run(store.save("s2", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is False
    assert asyncio.run(store.save_focus(
        "s2", {"last_poi": "new"}, owner_user_id="u1")) is False

    # The failed durable delete is reported honestly, but stale process-local
    # fallback copies are still removed so they cannot revive later.
    assert asyncio.run(store.delete_owner("u1")) is False
    assert store._mem == {}
    assert store._focus_mem == {}


def test_memory_store_is_not_a_shared_privacy_backend():
    store = SessionStore(redis_url="")

    assert asyncio.run(store.shared_backend_ready()) is False


def test_configured_but_unreachable_store_is_not_a_shared_privacy_backend():
    store = SessionStore(redis_url="redis://configured-but-unavailable:6379/0")

    async def unavailable():
        return None

    store._redis = unavailable
    assert asyncio.run(store.shared_backend_ready()) is False


def test_configured_connected_redis_is_a_shared_privacy_backend():
    class ConnectedRedis:
        async def ping(self):
            return True

    store = SessionStore(redis_url="redis://shared:6379/0")
    connected = ConnectedRedis()

    async def available():
        return connected

    store._redis = available
    assert asyncio.run(store.shared_backend_ready()) is True


def test_successful_owner_delete_keeps_idempotent_tombstone_for_pending_ttl():
    store = SessionStore(redis_url="")
    assert asyncio.run(store.save("s1", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is True

    assert asyncio.run(store.delete_owner("u1")) is True
    assert asyncio.run(store.delete_owner("u1")) is True
    assert asyncio.run(store.save("late", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is False
    assert asyncio.run(store.save_focus(
        "late", {"last_poi": "late"}, owner_user_id="u1")) is False


def test_active_tombstone_hides_stale_process_local_pending_and_focus():
    store = SessionStore(redis_url="")
    assert asyncio.run(store.save("s1", SessionState(
        phase="wait_confirm", owner_user_id="u1"))) is True
    assert asyncio.run(store.save_focus(
        "s1", {"last_poi": "private"}, owner_user_id="u1")) is True
    store._owner_fences["u1"] = time.time() + 300

    assert asyncio.run(store.load("s1", owner_user_id="u1")) is None
    assert asyncio.run(store.load_focus(
        "s1", owner_user_id="u1")) is None
