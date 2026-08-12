"""Opt-in SessionStore privacy integration tests against Compose Redis 7."""
from __future__ import annotations

import os
import secrets

import pytest

from orchestrator.cloud.models import SessionState
from orchestrator.cloud.session import SessionStore


_REDIS_URL = os.getenv(
    "PLANNER_REDIS_INTEGRATION_URL", "redis://127.0.0.1:6379/15")


async def _redis7_or_skip():
    client = None
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(
            _REDIS_URL, decode_responses=True, socket_connect_timeout=1,
            socket_timeout=1)
        await client.ping()
        version = str((await client.info("server")).get("redis_version") or "")
    except Exception as exc:
        if client is not None:
            await client.aclose()
        pytest.skip(
            "Redis 7 integration unavailable at "
            f"{_REDIS_URL}: {type(exc).__name__}; start Compose redis")
    if not version.startswith("7."):
        await client.aclose()
        pytest.skip(f"Redis 7 required, found {version or 'unknown'}")
    return client


@pytest.mark.asyncio
async def test_real_redis7_owner_isolation_and_persistent_delete_tombstone():
    redis = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"planner-owner-{nonce}"
    control_owner = f"planner-control-{nonce}"
    session = f"planner-session-{nonce}"
    control_session = session  # adversarially reuse the caller-controlled id
    store = SessionStore(redis_url=_REDIS_URL)
    store._r = redis
    session_key = store._session_key(owner, session)
    control_key = store._session_key(control_owner, control_session)
    focus_key = store._focus_key(owner, session)
    control_focus_key = store._focus_key(control_owner, control_session)
    keys = [
        session_key,
        control_key,
        focus_key,
        control_focus_key,
        store._owner_key(owner),
        store._owner_key(control_owner),
        store._owner_fence_key(owner),
        store._owner_fence_key(control_owner),
    ]
    try:
        assert await store.save(session, SessionState(
            phase="wait_confirm", owner_user_id=owner,
            ttl_seconds=420)) is True
        assert await store.save(control_session, SessionState(
            phase="wait_confirm", owner_user_id=control_owner)) is True
        assert await store.save_focus(
            session, {"last_poi": "private"}, owner_user_id=owner) is True
        assert await store.save_focus(
            control_session, {"last_poi": "control"},
            owner_user_id=control_owner) is True

        assert owner not in session_key and session not in session_key
        assert await store.load(
            session, owner_user_id=owner) is not None
        assert await store.load(
            session, owner_user_id=control_owner) is not None
        assert await store.clear(
            session, owner_user_id="foreign-owner") is False

        # Reads participate in the same fence as writes: once deletion owns
        # the boundary, a stale pending confirmation cannot be resumed.
        await redis.set(store._owner_fence_key(owner), "deleting", ex=300)
        assert await store.load(session, owner_user_id=owner) is None
        assert await store.load_focus(
            session, owner_user_id=owner) is None
        await redis.delete(store._owner_fence_key(owner))

        assert await store.delete_owner(owner) is True
        first_tombstone_ttl = await redis.ttl(
            store._owner_fence_key(owner))
        assert first_tombstone_ttl > 300
        assert await store.delete_owner(owner) is True
        assert await redis.exists(session_key) == 0
        assert await redis.exists(focus_key) == 0
        assert await redis.exists(store._owner_key(owner)) == 0
        assert await redis.get(store._owner_fence_key(owner)) == "deleted"
        assert await redis.ttl(
            store._owner_fence_key(owner)) >= first_tombstone_ttl - 2
        assert await store.save(session, SessionState(
            phase="wait_confirm", owner_user_id=owner)) is False
        assert await store.load(
            control_session, owner_user_id=control_owner) is not None
        control = await store.load_focus(
            control_session, owner_user_id=control_owner)
        assert control is not None and control["last_poi"] == "control"
    finally:
        await redis.delete(*keys)
        await redis.aclose()
