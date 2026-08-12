"""Opt-in integration tests against the Compose Redis 7 service.

Run after ``docker compose -f compose.yaml up -d redis``.  A non-default Redis
database and unique keys keep the test isolated from the application database.
"""
from __future__ import annotations

import asyncio
import os
import secrets

import pytest

from agents.mcp_bridge.src.merchant.drafts import RedisDraftStore
from agents.mcp_bridge.src.merchant.models import MerchantDraft, MerchantItem


_REDIS_URL = os.getenv(
    "MERCHANT_REDIS_INTEGRATION_URL", "redis://127.0.0.1:6379/15")


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
            f"{_REDIS_URL}: {type(exc).__name__}; start the Compose redis service")
    if not version.startswith("7."):
        await client.aclose()
        pytest.skip(f"Redis 7 required, found {version or 'unknown'}")
    return client, version


def _draft(token: str, owner: str, session: str, *, operation="create",
           merchant="integration"):
    return MerchantDraft(
        token=token, merchant=merchant, operation=operation,
        user_id=owner, session_id=session,
        store={"id": "S1", "name": "integration"},
        items=[MerchantItem(name="item", quantity=1)], amount_cents=100,
        upstream_args={"items": []}, schema_digest="schema", created_at=1.0)


@pytest.mark.asyncio
async def test_real_redis7_ttl_and_action_checked_lua_consume():
    redis, version = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    session = f"session-{nonce}"
    token = f"token-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    draft_key = f"mcp:merchant:draft:{token}"
    current_key = store._current_key(owner, session, "integration")
    try:
        assert version.startswith("7.")
        assert await store.put(_draft(token, owner, session)) is True
        assert 0 < await redis.ttl(draft_key) <= 30
        assert 0 < await redis.ttl(current_key) <= 30

        # The action mismatch is checked in the same Lua script as deletion.
        assert await store.consume(
            token, user_id=owner, session_id=session, merchant="integration",
            expected_action="cancel") is None
        assert await redis.exists(draft_key) == 1
        assert await redis.exists(current_key) == 1

        results = await asyncio.gather(*[
            store.consume(
                token, user_id=owner, session_id=session,
                merchant="integration", expected_action="create")
            for _ in range(8)
        ])
        assert sum(result is not None for result in results) == 1
        consumed = next(result for result in results if result is not None)
        assert await store.authorize(consumed.token, user_id=owner) is True
        assert await store.release(consumed.token, user_id=owner) is True
        assert await redis.exists(draft_key) == 0
        assert await redis.exists(current_key) == 0

        expiring = f"expiry-{nonce}"
        expiring_draft_key = f"mcp:merchant:draft:{expiring}"
        assert await store.put(_draft(expiring, owner, session)) is True
        await redis.expire(expiring_draft_key, 1)
        await redis.expire(current_key, 1)
        await asyncio.sleep(1.1)
        assert await store.consume_current(
            user_id=owner, session_id=session, merchant="integration",
            expected_action="create") is None
    finally:
        await redis.delete(draft_key, current_key,
                           f"mcp:merchant:draft:expiry-{nonce}",
                           store._owner_key(owner),
                           store._owner_meta_key(owner),
                           store._owner_fence_key(owner),
                           store._owner_active_key(owner))
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_active_lease_linearizes_confirm_and_privacy_delete():
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    session = f"session-{nonce}"
    token = f"lease-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    keys = [
        f"mcp:merchant:draft:{token}",
        store._current_key(owner, session, "integration"),
        store._owner_key(owner),
        store._owner_meta_key(owner),
        store._owner_fence_key(owner),
        store._owner_active_key(owner),
    ]
    try:
        assert await store.put(_draft(token, owner, session)) is True
        draft = await store.consume(
            token, user_id=owner, session_id=session, merchant="integration",
            expected_action="create")
        assert draft is not None
        assert 0 < await redis.ttl(store._owner_active_key(owner)) <= 30

        # Operation linearized first: deletion becomes pending without ACK.
        assert await store.delete_owner(owner) is False
        await redis.expire(store._owner_active_key(owner), 1)
        await redis.expire(store._owner_fence_key(owner), 1)
        assert await store.authorize(token, user_id=owner) is True
        assert 1 < await redis.ttl(store._owner_active_key(owner)) <= 30
        assert 1 < await redis.ttl(store._owner_fence_key(owner)) <= 30
        assert await store.release("wrong", user_id=owner) is False
        assert await store.release(token, user_id=f"other-{owner}") is False
        assert await store.release(token, user_id=owner) is True
        assert await store.delete_owner(owner) is True
        assert await redis.get(store._owner_fence_key(owner)) == "privacy_deleted"
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_crashed_lease_expires_before_delete_retry():
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    session = f"session-{nonce}"
    token = f"crash-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    keys = [
        f"mcp:merchant:draft:{token}",
        store._current_key(owner, session, "integration"),
        store._owner_key(owner), store._owner_meta_key(owner),
        store._owner_fence_key(owner), store._owner_active_key(owner),
    ]
    try:
        assert await store.put(_draft(token, owner, session)) is True
        assert await store.consume(
            token, user_id=owner, session_id=session, merchant="integration",
            expected_action="create") is not None
        assert await store.delete_owner(owner) is False

        # Model a crashed worker without waiting for the production TTL.
        await redis.expire(store._owner_active_key(owner), 1)
        await redis.expire(store._owner_fence_key(owner), 1)
        await asyncio.sleep(1.1)
        assert await store.authorize(token, user_id=owner) is False
        assert await store.delete_owner(owner) is True
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_operation_hold_renews_lease_during_blocked_effect():
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    session = f"session-{nonce}"
    token = f"hold-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    store._renew_interval_seconds = 0.25
    keys = [
        f"mcp:merchant:draft:{token}",
        store._current_key(owner, session, "integration"),
        store._owner_key(owner), store._owner_meta_key(owner),
        store._owner_fence_key(owner), store._owner_active_key(owner),
    ]
    try:
        assert await store.put(_draft(token, owner, session)) is True
        assert await store.consume(
            token, user_id=owner, session_id=session, merchant="integration",
            expected_action="create") is not None

        async with store.operation_hold(token, user_id=owner) as held:
            assert held is True
            await redis.expire(store._owner_active_key(owner), 1)
            await asyncio.sleep(1.2)
            assert await redis.exists(store._owner_active_key(owner)) == 1
            assert await store.delete_owner(owner) is False

        assert await store.delete_owner(owner) is True
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_owner_delete_is_scoped_and_idempotent():
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    control_owner = f"control-{nonce}"
    tokens = [f"target-a-{nonce}", f"target-b-{nonce}", f"control-{nonce}"]
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    keys = [f"mcp:merchant:draft:{token}" for token in tokens]
    keys.extend([
        store._current_key(owner, f"session-{nonce}-a", "integration"),
        store._current_key(owner, f"session-{nonce}-b", "mcdonalds"),
        store._current_key(control_owner, f"session-{nonce}", "integration"),
        store._owner_key(owner),
        store._owner_key(control_owner),
        store._owner_meta_key(owner),
        store._owner_meta_key(control_owner),
        store._owner_fence_key(owner),
        store._owner_fence_key(control_owner),
        store._owner_active_key(owner),
        store._owner_active_key(control_owner),
    ])
    try:
        assert await store.put(
            _draft(tokens[0], owner, f"session-{nonce}-a")) is True
        assert await store.put(
            _draft(tokens[1], owner, f"session-{nonce}-b",
                   merchant="mcdonalds", operation="cancel")) is True
        assert await store.put(
            _draft(tokens[2], control_owner, f"session-{nonce}")) is True

        assert await store.delete_owner(owner) is True
        assert await store.count_owner(owner) == 0
        assert await redis.get(store._owner_fence_key(owner)) == "privacy_deleted"
        assert 0 < await redis.ttl(store._owner_fence_key(owner)) <= 30
        assert await store.delete_owner(owner) is True
        assert await store.count_owner(control_owner) > 0
        control = await store.consume_current(
            user_id=control_owner, session_id=f"session-{nonce}",
            merchant="integration", expected_action="create")
        assert control is not None and control.token == tokens[2]
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_privacy_tombstone_blocks_request_started_before_delete():
    """A request admitted before deletion cannot commit after deletion ACK."""
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    session = f"session-{nonce}"
    token = f"late-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    started = asyncio.Event()
    release = asyncio.Event()

    async def stale_request():
        started.set()
        await release.wait()
        return await store.put(_draft(token, owner, session))

    task = asyncio.create_task(stale_request())
    keys = [
        f"mcp:merchant:draft:{token}",
        store._current_key(owner, session, "integration"),
        store._owner_key(owner),
        store._owner_meta_key(owner),
        store._owner_fence_key(owner),
        store._owner_active_key(owner),
    ]
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert await store.delete_owner(owner) is True
        release.set()
        assert await asyncio.wait_for(task, timeout=2) is False
        assert await store.count_owner(owner) == 0
        assert await redis.exists(keys[0]) == 0
        assert await redis.exists(keys[1]) == 0
    finally:
        release.set()
        if not task.done():
            await task
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_owner_delete_repairs_foreign_member_and_pointer():
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    pointer_owner = f"pointer-owner-{nonce}"
    control_owner = f"control-{nonce}"
    target_token = f"target-{nonce}"
    pointer_token = f"pointer-{nonce}"
    control_token = f"control-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    target_draft = f"mcp:merchant:draft:{target_token}"
    pointer_draft = f"mcp:merchant:draft:{pointer_token}"
    control_draft = f"mcp:merchant:draft:{control_token}"
    target_current = store._current_key(
        owner, f"session-{nonce}", "integration")
    pointer_current = store._current_key(
        pointer_owner, f"session-{nonce}", "integration")
    keys = [
        target_draft,
        pointer_draft,
        control_draft,
        target_current,
        pointer_current,
        store._current_key(control_owner, f"session-{nonce}", "integration"),
        store._owner_key(owner),
        store._owner_key(pointer_owner),
        store._owner_key(control_owner),
        store._owner_meta_key(owner),
        store._owner_meta_key(pointer_owner),
        store._owner_meta_key(control_owner),
        store._owner_fence_key(owner),
        store._owner_fence_key(pointer_owner),
        store._owner_fence_key(control_owner),
        store._owner_active_key(owner),
        store._owner_active_key(pointer_owner),
        store._owner_active_key(control_owner),
    ]
    try:
        assert await store.put(_draft(
            target_token, owner, f"session-{nonce}")) is True
        assert await store.put(_draft(
            control_token, control_owner, f"session-{nonce}")) is True

        await redis.sadd(store._owner_key(owner), control_draft)
        assert await store.delete_owner(owner) is True
        assert await redis.exists(target_draft) == 0
        assert await redis.exists(control_draft) == 1

        assert await store.put(_draft(
            pointer_token, pointer_owner, f"session-{nonce}")) is True
        await redis.set(pointer_current, control_token, ex=30)
        assert await store.delete_owner(pointer_owner) is True
        assert await redis.exists(pointer_draft) == 0
        assert await redis.exists(control_draft) == 1
        control = await store.consume_current(
            user_id=control_owner, session_id=f"session-{nonce}",
            merchant="integration", expected_action="create")
        assert control is not None and control.token == control_token
    finally:
        await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_real_redis7_missing_index_and_legacy_marker_are_scanned():
    redis, _ = await _redis7_or_skip()
    nonce = secrets.token_hex(12)
    owner = f"owner-{nonce}"
    legacy_owner = f"legacy-owner-{nonce}"
    session = f"session-{nonce}"
    token = f"orphan-{nonce}"
    legacy_token = f"legacy-{nonce}"
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    draft_key = f"mcp:merchant:draft:{token}"
    current_key = store._current_key(owner, session, "integration")
    owner_key = store._owner_key(owner)
    legacy_draft_key = f"mcp:merchant:draft:{legacy_token}"
    keys = [
        draft_key, legacy_draft_key, current_key, owner_key,
        store._owner_meta_key(owner), store._owner_fence_key(owner),
        store._owner_active_key(owner),
        store._current_key(legacy_owner, session, "integration"),
        store._owner_key(legacy_owner), store._owner_meta_key(legacy_owner),
        store._owner_fence_key(legacy_owner),
        store._owner_active_key(legacy_owner)]
    try:
        assert await store.put(_draft(token, owner, session)) is True
        await redis.delete(owner_key)

        assert await store.count_owner(owner) == -1
        assert await store.delete_owner(owner) is True
        assert await redis.exists(draft_key) == 0
        assert await redis.exists(current_key) == 0
        assert await store.count_owner(owner) == 0

        assert await store.put(_draft(
            legacy_token, legacy_owner, session)) is True
        await redis.delete(store._owner_key(legacy_owner),
                           store._owner_meta_key(legacy_owner))
        assert await store.count_owner(legacy_owner) == -1
        assert await store.delete_owner(legacy_owner) is True
        assert await redis.exists(legacy_draft_key) == 0
        assert await redis.exists(
            store._current_key(legacy_owner, session, "integration")) == 0
    finally:
        await redis.delete(*keys)
        await redis.aclose()
