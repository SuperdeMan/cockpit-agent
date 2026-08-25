"""商户复合工作流公共契约：typed model、Redis 草稿与安全交互。"""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agents.mcp_bridge.src.merchant.models import (
    MerchantChoice,
    MerchantDraft,
    MerchantItem,
    MerchantResult,
    yuan_to_cents,
)
from agents.mcp_bridge.src.merchant.drafts import RedisDraftStore
from agents.mcp_bridge.src.merchant.base import (
    DeclaredBusinessIncomplete,
    DeclaredBusinessRejected,
    MerchantWorkflow,
)
from agents.mcp_bridge.src import agent as agent_module
from agents.mcp_bridge.src.admission import (
    ServerSpec,
    ToolSpec,
    WorkflowSpec,
    schema_fingerprint,
)
from agents.mcp_bridge.src.agent import McpBridgeAgent, _Binding


def _draft(token="opaque", *, merchant="luckin", user="u1", session="s1",
           operation="create"):
    return MerchantDraft(
        token=token,
        merchant=merchant,
        operation=operation,
        user_id=user,
        session_id=session,
        store={"id": "602825", "name": "人民广场店"},
        items=[MerchantItem(name="生椰拿铁", quantity=1, sku="SP1",
                            specifications=["大杯", "热", "少甜"])],
        amount_cents=2000,
        upstream_args={"deptId": 602825, "productList": []},
        schema_digest="sha",
        created_at=1.0,
    )


class FakeRedis:
    """只实现 DraftStore 消费的 Redis 子集；eval 内持锁模拟 Lua 原子性。"""

    def __init__(self):
        self.values = {}
        self.expiries = {}
        self.sets = {}
        self.hashes = {}
        self.available = True
        self._lock = asyncio.Lock()
        self.pause_put_token = ""
        self.put_entered = asyncio.Event()
        self.release_put = asyncio.Event()

    async def ping(self):
        if not self.available:
            raise OSError("redis down")
        return True

    async def set(self, key, value, ex=None, nx=False):
        if not self.available:
            raise OSError("redis down")
        if nx and (key in self.values or key in self.sets or key in self.hashes):
            return None
        self.values[key] = value
        self.expiries[key] = ex
        return True

    async def get(self, key):
        if not self.available:
            raise OSError("redis down")
        return self.values.get(key)

    async def smembers(self, key):
        if not self.available:
            raise OSError("redis down")
        return set(self.sets.get(key, set()))

    async def exists(self, key):
        if not self.available:
            raise OSError("redis down")
        return int(key in self.values or key in self.sets or key in self.hashes)

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(
            {str(name): str(value) for name, value in mapping.items()})
        return len(mapping)

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def expire(self, key, ttl):
        if key in self.values or key in self.sets or key in self.hashes:
            self.expiries[key] = ttl
            return True
        return False

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(key in self.values or key in self.sets or
                           key in self.hashes)
            self.values.pop(key, None)
            self.sets.pop(key, None)
            self.hashes.pop(key, None)
            self.expiries.pop(key, None)
        return removed

    async def scan(self, cursor=0, match=None, count=128):
        del cursor, count
        keys = set(self.values) | set(self.sets) | set(self.hashes)
        if match and match.endswith("*"):
            keys = {key for key in keys if key.startswith(match[:-1])}
        elif match:
            keys = {key for key in keys if key == match}
        return 0, sorted(keys)

    def _validate_owner_index(self, owner_key, meta_key, draft_prefix,
                              current_prefix, owner, version):
        members = self.sets.get(owner_key, set())
        meta = self.hashes.get(meta_key, {})
        if meta.get("version") != version:
            return None
        cardinality = len(members)
        expected = int(meta.get("count", -1))
        if meta.get("state") in {"idle", "privacy_deleted"}:
            return [] if expected == 0 and cardinality == 0 else None
        if meta.get("state") != "active" or not cardinality or expected != cardinality:
            return None
        live = []
        for key in members:
            if key.startswith(draft_prefix):
                raw = self.values.get(key)
                if raw is None:
                    continue
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError):
                    return None
                if value.get("owner_digest") != owner:
                    return None
                live.append(key)
            elif key.startswith(current_prefix):
                token = self.values.get(key)
                if token is None:
                    continue
                draft_key = draft_prefix + token
                if draft_key not in members:
                    return None
                raw = self.values.get(draft_key)
                if raw is None:
                    return None
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError):
                    return None
                if value.get("owner_digest") != owner:
                    return None
                canonical = (f"{current_prefix}{owner}:"
                             f"{value.get('session_digest')}:"
                             f"{str(value.get('merchant')).lower()}")
                if key != canonical:
                    return None
                live.append(key)
            else:
                return None
        return live

    async def eval(self, script, numkeys, *args):
        if not self.available:
            raise OSError("redis down")
        if "merchant-draft-put-v3" in script and args[7] == self.pause_put_token:
            self.put_entered.set()
            await self.release_put.wait()
        async with self._lock:
            if "merchant-draft-put-v3" in script:
                (draft_key, current_key, owner_key, meta_key, fence_key,
                 active_key, raw, token, ttl, version, lease_token) = args
                if fence_key in self.values:
                    return 0
                if (lease_token and
                        self.values.get(active_key) != lease_token):
                    return 0
                if not lease_token and active_key in self.values:
                    return 0
                cardinality = len(self.sets.get(owner_key, set()))
                meta = self.hashes.get(meta_key)
                if meta is not None:
                    if (meta.get("version") != version or
                            int(meta.get("count", -1)) != cardinality or
                            (meta.get("state") not in {"active", "idle"}) or
                            (meta.get("state") == "idle" and cardinality)):
                        return 0
                elif (cardinality or draft_key in self.values or
                      current_key in self.values):
                    return 0
                self.values[draft_key] = raw
                self.values[current_key] = token
                self.expiries[draft_key] = ttl
                self.expiries[current_key] = ttl
                self.expiries[owner_key] = ttl
                self.sets.setdefault(owner_key, set()).update(
                    {draft_key, current_key})
                self.hashes[meta_key] = {
                    "version": version, "state": "active",
                    "count": str(len(self.sets[owner_key]))}
                self.expiries[meta_key] = ttl
                return 1
            if "merchant-draft-consume-v3" in script:
                (draft_key, current_key, owner_key, meta_key, fence_key,
                 active_key, token, owner, session, merchant,
                 expected_action, ttl, version) = args
                requires_lease = expected_action in {"create", "cancel"}
                if fence_key in self.values:
                    return ""
                if requires_lease and active_key in self.values:
                    return ""
                live = self._validate_owner_index(
                    owner_key, meta_key, "mcp:merchant:draft:",
                    "mcp:merchant:current:", owner, version)
                raw = self.values.get(draft_key)
                if (live is None or draft_key not in self.sets.get(owner_key, set())
                        or current_key not in self.sets.get(owner_key, set())
                        or not raw or self.values.get(current_key) != token):
                    return ""
                data = json.loads(raw)
                if (data["owner_digest"] != owner or
                        data["session_digest"] != session or
                        data["merchant"] != merchant or
                        data["operation"] != expected_action):
                    return ""
                if requires_lease:
                    self.values[active_key] = token
                    self.expiries[active_key] = ttl
                self.values.pop(draft_key, None)
                self.values.pop(current_key, None)
                members = self.sets.get(owner_key, set())
                members.difference_update({draft_key, current_key})
                if not members:
                    self.sets.pop(owner_key, None)
                    self.hashes[meta_key] = {
                        "version": version, "state": "idle", "count": "0"}
                    self.expiries[meta_key] = ttl
                else:
                    self.hashes[meta_key]["count"] = str(len(members))
                return raw
            if "merchant-draft-count-owner-v2" in script:
                (owner_key, meta_key, draft_prefix, current_prefix,
                 owner, version) = args
                live = self._validate_owner_index(
                    owner_key, meta_key, draft_prefix, current_prefix,
                    owner, version)
                return [-1, 0] if live is None else [1, len(live)]
            if "merchant-draft-session-lifecycle-v1" in script:
                (owner_key, meta_key, fence_key, active_key, draft_prefix,
                 current_prefix, owner, session, version, mode, ttl) = args
                if fence_key in self.values or active_key in self.values:
                    return [-2, 0, -1]
                live = self._validate_owner_index(
                    owner_key, meta_key, draft_prefix, current_prefix,
                    owner, version)
                if live is None:
                    return [-1, 0, -1]
                targets = []
                drafts = 0
                for key in tuple(self.sets.get(owner_key, set())):
                    if key.startswith(draft_prefix):
                        raw = self.values.get(key)
                        if raw is None:
                            return [-1, 0, -1]
                        value = json.loads(raw)
                        if value.get("session_digest") == session:
                            targets.append(key)
                            drafts += 1
                    elif key.startswith(current_prefix):
                        token = self.values.get(key)
                        raw = self.values.get(draft_prefix + str(token or ""))
                        if raw is None:
                            return [-1, 0, -1]
                        value = json.loads(raw)
                        if value.get("session_digest") == session:
                            targets.append(key)
                if mode == "count":
                    return [1, drafts, len(self.sets.get(owner_key, set()))]
                if mode != "discard":
                    return [-1, 0, -1]
                for key in targets:
                    if self.values.pop(key, None) is None:
                        return [-1, 0, -1]
                    self.expiries.pop(key, None)
                    self.sets[owner_key].remove(key)
                remaining = len(self.sets.get(owner_key, set()))
                if remaining:
                    self.hashes[meta_key]["count"] = str(remaining)
                else:
                    self.sets.pop(owner_key, None)
                    self.hashes[meta_key] = {
                        "version": version, "state": "idle", "count": "0"}
                    self.expiries[meta_key] = ttl
                return [1, drafts, remaining]
            if "merchant-draft-delete-owner-v3" in script:
                (owner_key, meta_key, active_key, draft_prefix,
                 current_prefix, owner, ttl, version) = args
                if active_key in self.values:
                    return [-2, 0, 0]
                live = self._validate_owner_index(
                    owner_key, meta_key, draft_prefix, current_prefix,
                    owner, version)
                if live is None:
                    return [-1, 0, 0]
                removed = 0
                for key in tuple(self.sets.get(owner_key, set())):
                    removed += int(self.values.pop(key, None) is not None)
                self.sets.pop(owner_key, None)
                self.hashes[meta_key] = {
                    "version": version, "state": "privacy_deleted",
                    "count": "0"}
                self.expiries[meta_key] = ttl
                return ([1, removed, len(live)] if removed == len(live)
                        else [-1, removed, len(live)])
            if "merchant-draft-authorize-lease-v1" in script:
                active_key, fence_key, token, tombstone, ttl = args
                if self.values.get(active_key) != token:
                    return 0
                if self.values.get(fence_key) == tombstone:
                    return 0
                self.expiries[active_key] = ttl
                if fence_key in self.values:
                    self.expiries[fence_key] = ttl
                return 1
            if "merchant-draft-release-lease-v1" in script:
                active_key, token = args
                if self.values.get(active_key) != token:
                    return 0
                self.values.pop(active_key, None)
                self.expiries.pop(active_key, None)
                return 1
            if "GET', KEYS[1]) == ARGV[1]" in script:
                fence_key, active_key, token, tombstone, ttl = args
                if (self.values.get(fence_key) == token and
                        active_key not in self.values):
                    self.values[fence_key] = tombstone
                    self.expiries[fence_key] = ttl
                    return 1
                return 0
            raise AssertionError("unexpected Lua script")


def test_merchant_drafts_are_declared_as_deletable_personal_data():
    target = next(
        item for item in agent_module.PERSONAL_DATA_TARGETS
        if item["id"] == "merchant_draft"
    )
    assert target["storage_variants"] == (
        "mcp:merchant:draft:*",
        "mcp:merchant:current:*",
        "mcp:merchant:owner:*",
    )


def test_models_use_cents_and_ledger_allowlist():
    assert yuan_to_cents("20.005") == 2001
    assert yuan_to_cents(20.004) == 2000
    result = MerchantResult(
        server="luckin", merchant="luckin", order_id="O1", status="created",
        amount_cents=2000, store_name="人民广场店",
        pay_url="https://secret.invalid", items=[{"phone": "13800000000"}],
        raw={"couponCodeList": ["private"]},
    )
    assert result.ledger_ref() == {
        "server": "luckin", "merchant": "luckin", "order_id": "O1",
        "status": "created", "amount_cents": 2000,
        "store_name": "人民广场店",
    }


def test_choice_and_preview_cards_are_deterministic_and_have_no_confirm_action():
    class _LuckinWorkflow(MerchantWorkflow):
        merchant = "luckin"

    choice = MerchantChoice(id="p1", name="生椰拿铁", subtitle="20 元",
                            send_text="选第一个生椰拿铁")
    card = _LuckinWorkflow.choice_card("product", [choice])
    assert card["type"] == "merchant_choices"
    assert card["merchant"] == "luckin"
    assert card["buttons"] == [{"label": "生椰拿铁", "send_text": "选第一个生椰拿铁"}]

    preview = MerchantWorkflow.preview_card(_draft())
    assert preview["type"] == "merchant_order_preview"
    assert preview["confirmation_context"] == "merchant_create"
    assert preview.get("buttons", []) == []
    assert "opaque" not in json.dumps(preview, ensure_ascii=False)


@pytest.mark.asyncio
async def test_draft_owner_session_merchant_isolation_and_single_consume():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    await store.put(_draft())

    raw = next(value for key, value in redis.values.items()
               if key.startswith("mcp:merchant:draft:"))
    assert "opaque" not in raw, "checkout token 只能存在 Redis key/current pointer"
    assert all(expiry == 600 for expiry in redis.expiries.values())

    assert await store.consume("opaque", user_id="u2", session_id="s1",
                               merchant="luckin", expected_action="create") is None
    assert await store.consume("opaque", user_id="u1", session_id="s1",
                               merchant="mcdonalds", expected_action="create") is None
    got = await store.consume("opaque", user_id="u1", session_id="s1",
                              merchant="luckin", expected_action="create")
    assert got and got.amount_cents == 2000
    assert await store.authorize(got.token, user_id=got.user_id) is True
    assert await store.consume("opaque", user_id="u1", session_id="s1",
                               merchant="luckin", expected_action="create") is None
    assert await store.release(got.token, user_id=got.user_id) is True


@pytest.mark.asyncio
async def test_delete_wins_before_consume_and_no_active_lease_is_created():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("delete-wins", user="u1")) is True

    assert await store.delete_owner("u1") is True
    consumed = await store.consume(
        "delete-wins", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create")

    assert consumed is None
    assert await redis.exists(store._owner_active_key("u1")) == 0


@pytest.mark.asyncio
async def test_active_operation_makes_delete_pending_until_exact_release():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("operation-wins", user="u1")) is True
    draft = await store.consume(
        "operation-wins", user_id="u1", session_id="s1",
        merchant="luckin", expected_action="create")
    assert draft is not None

    active_key = store._owner_active_key("u1")
    assert redis.values[active_key] == "operation-wins"
    assert redis.expiries[active_key] == 600
    assert await store.delete_owner("u1") is False
    fence_key = store._owner_fence_key("u1")
    redis.expiries[active_key] = 1
    redis.expiries[fence_key] = 1
    assert await store.authorize("operation-wins", user_id="u1") is True
    assert redis.expiries[active_key] == 600
    assert redis.expiries[fence_key] == 600
    assert await store.release("wrong-token", user_id="u1") is False
    assert await store.release("operation-wins", user_id="u2") is False
    assert redis.values[active_key] == "operation-wins"
    assert await store.release("operation-wins", user_id="u1") is True
    assert await redis.exists(active_key) == 0
    assert await store.delete_owner("u1") is True


@pytest.mark.asyncio
async def test_active_lease_can_atomically_publish_a_fresh_confirmation_snapshot():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("old", user="u1")) is True
    old = await store.consume(
        "old", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create")
    assert old is not None

    fresh = replace(old, token="fresh")
    assert await store.put(fresh, lease_token=old.token) is True
    assert await store.authorize(old.token, user_id="u1") is True
    assert await store.release(old.token, user_id="u1") is True
    assert await store.consume(
        "fresh", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create") is not None


@pytest.mark.asyncio
async def test_expired_or_wrong_lease_cannot_publish_fresh_snapshot():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("old", user="u1")) is True
    old = await store.consume(
        "old", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create")
    assert old is not None
    fresh = replace(old, token="fresh")

    assert await store.put(fresh, lease_token="wrong") is False
    assert await store.release(old.token, user_id=old.user_id) is True
    assert await store.put(fresh, lease_token=old.token) is False


@pytest.mark.asyncio
async def test_operation_hold_cancels_owner_when_lease_is_lost():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=30)
    store._renew_interval_seconds = 0.01
    assert await store.put(_draft("lost", user="u1")) is True
    draft = await store.consume(
        "lost", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create")
    assert draft is not None
    entered = asyncio.Event()
    release = asyncio.Event()
    committed = False

    async def operation():
        nonlocal committed
        async with store.operation_hold(draft.token, user_id=draft.user_id):
            entered.set()
            await release.wait()
            committed = True

    task = asyncio.create_task(operation())
    await asyncio.wait_for(entered.wait(), timeout=1)
    await redis.delete(store._owner_active_key("u1"))
    await asyncio.sleep(0.05)

    assert task.done() is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert committed is False
    assert await store.delete_owner("u1") is True


@pytest.mark.asyncio
async def test_consume_and_privacy_fence_have_one_atomic_winner():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("race", user="u1")) is True

    consumed, deleted = await asyncio.gather(
        store.consume(
            "race", user_id="u1", session_id="s1", merchant="luckin",
            expected_action="create"),
        store.delete_owner("u1"),
    )

    if consumed is None:
        assert deleted is True
        assert await redis.exists(store._owner_active_key("u1")) == 0
    else:
        assert deleted is False
        assert await store.authorize(consumed.token, user_id="u1") is True
        assert await store.release(consumed.token, user_id="u1") is True
        assert await store.delete_owner("u1") is True


@pytest.mark.asyncio
async def test_wrong_action_does_not_consume_or_delete_draft():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft(operation="cancel")) is True

    assert await store.consume(
        "opaque", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create") is None
    assert any(key.startswith("mcp:merchant:draft:") for key in redis.values)
    assert any(key.startswith("mcp:merchant:current:") for key in redis.values)

    consumed = await store.consume(
        "opaque", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="cancel")
    assert consumed is not None and consumed.operation == "cancel"
    assert await store.release(consumed.token, user_id=consumed.user_id) is True


@pytest.mark.asyncio
async def test_discard_releases_write_lease_instead_of_blocking_privacy_delete():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("discarded", user="u1")) is True
    assert await store.discard(
        "discarded", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create") is True
    assert await redis.exists(store._owner_active_key("u1")) == 0
    assert await store.delete_owner("u1") is True


@pytest.mark.asyncio
async def test_session_draft_count_and_discard_are_exact_and_preserve_other_sessions():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("s1-luckin", user="u1", session="s1")) is True
    assert await store.put(_draft(
        "s1-mcd", user="u1", session="s1", merchant="mcdonalds")) is True
    assert await store.put(_draft("s2-control", user="u1", session="s2")) is True
    assert await store.put(_draft("u2-control", user="u2", session="s1")) is True

    assert await store.count_session("u1", "s1") == 2
    assert await store.discard_session("u1", "s1") == 2
    assert await store.count_session("u1", "s1") == 0
    assert await store.count_session("u1", "s2") == 1
    assert await store.count_session("u2", "s1") == 1
    assert await store.consume_current(
        user_id="u1", session_id="s2", merchant="luckin",
        expected_action="create") is not None


@pytest.mark.asyncio
async def test_session_draft_discard_fails_closed_during_active_write_lease():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("active", user="u1", session="s1")) is True
    draft = await store.consume(
        "active", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create")
    assert draft is not None

    assert await store.discard_session("u1", "s1") == -1
    assert await store.release(draft.token, user_id=draft.user_id) is True


@pytest.mark.asyncio
async def test_current_pointer_rejects_old_preview_and_concurrent_double_consume():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    await store.put(_draft("old"))
    await store.put(_draft("new"))

    assert await store.consume("old", user_id="u1", session_id="s1",
                               merchant="luckin", expected_action="create") is None
    results = await asyncio.gather(*[
        store.consume_current(
            user_id="u1", session_id="s1", merchant="luckin",
            expected_action="create")
        for _ in range(5)
    ])
    assert sum(result is not None for result in results) == 1
    assert next(result for result in results if result is not None).token == "new"


@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed_without_memory_fallback():
    redis = FakeRedis()
    redis.available = False
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft()) is False
    assert await store.consume_current(user_id="u1", session_id="s1",
                                       merchant="luckin",
                                       expected_action="create") is None


@pytest.mark.asyncio
async def test_delete_owner_is_immediate_idempotent_and_preserves_control_owner():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("u1-first", user="u1", session="s1")) is True
    assert await store.put(_draft(
        "u1-second", user="u1", session="s2", merchant="mcdonalds",
        operation="cancel")) is True
    assert await store.put(_draft("u2-control", user="u2", session="s1")) is True

    assert await store.delete_owner("u1") is True
    assert await store.count_owner("u1") == 0
    fence_key = store._owner_fence_key("u1")
    assert redis.values[fence_key] == "privacy_deleted"
    assert redis.expiries[fence_key] == 600
    assert await store.delete_owner("u1") is True
    assert await store.count_owner("u2") > 0
    control = await store.consume_current(
        user_id="u2", session_id="s1", merchant="luckin",
        expected_action="create")
    assert control is not None and control.token == "u2-control"
    assert all("u1" not in key and "u2" not in key for key in redis.values)


@pytest.mark.asyncio
async def test_privacy_delete_tombstone_rejects_put_started_before_delete_finished():
    """A stale request must not recreate a checkout draft after delete ACK."""
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    redis.pause_put_token = "late"

    stale_put = asyncio.create_task(
        store.put(_draft("late", user="u1", session="s1")))
    await asyncio.wait_for(redis.put_entered.wait(), timeout=1)
    assert await store.delete_owner("u1") is True
    redis.release_put.set()

    assert await asyncio.wait_for(stale_put, timeout=1) is False
    assert await store.count_owner("u1") == 0
    assert not any(key.startswith("mcp:merchant:draft:late")
                   for key in redis.values)


@pytest.mark.asyncio
async def test_normal_consume_returns_owner_to_idle_and_allows_next_order():
    """Normal exactly-once consumption is not a privacy deletion tombstone."""
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis, ttl_seconds=600)
    assert await store.put(_draft("first", user="u1", session="s1")) is True
    consumed = await store.consume(
        "first", user_id="u1", session_id="s1", merchant="luckin",
        expected_action="create")
    assert consumed is not None
    assert await store.release(consumed.token, user_id=consumed.user_id) is True

    assert await store.put(_draft("second", user="u1", session="s2")) is True
    consumed = await store.consume(
        "second", user_id="u1", session_id="s2", merchant="luckin",
        expected_action="create")
    assert consumed is not None
    assert await store.release(consumed.token, user_id=consumed.user_id) is True


@pytest.mark.asyncio
async def test_delete_owner_repairs_foreign_member_without_deleting_control_owner():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("target", user="u1", session="s1")) is True
    assert await store.put(_draft("control", user="u2", session="s1")) is True
    redis.sets[store._owner_key("u1")].add("mcp:merchant:draft:control")
    assert await store.delete_owner("u1") is True
    assert await store.count_owner("u1") == 0
    assert "mcp:merchant:draft:target" not in redis.values
    assert "mcp:merchant:draft:control" in redis.values
    assert await store.count_owner("u2") > 0


@pytest.mark.asyncio
async def test_delete_owner_repairs_foreign_pointer_without_deleting_control_draft():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("target", user="u1", session="s1")) is True
    assert await store.put(_draft("control", user="u2", session="s1")) is True
    target_current = store._current_key("u1", "s1", "luckin")
    redis.values[target_current] = "control"
    assert await store.delete_owner("u1") is True
    assert await store.count_owner("u1") == 0
    assert "mcp:merchant:draft:target" not in redis.values
    assert "mcp:merchant:draft:control" in redis.values
    assert await store.count_owner("u2") > 0


@pytest.mark.asyncio
async def test_missing_owner_index_never_reports_empty_or_successful_deletion():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("orphan", user="u1", session="s1")) is True
    redis.sets.pop(store._owner_key("u1"), None)
    assert await store.count_owner("u1") == -1
    assert await store.delete_owner("u1") is True
    assert await store.count_owner("u1") == 0
    assert not any(key.startswith("mcp:merchant:draft:orphan")
                   for key in redis.values)


@pytest.mark.asyncio
async def test_unindexed_legacy_orphan_is_found_by_fenced_scan():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("legacy", user="legacy-owner")) is True
    redis.sets.pop(store._owner_key("legacy-owner"), None)
    redis.hashes.pop(store._owner_meta_key("legacy-owner"), None)

    assert await store.count_owner("legacy-owner") == -1
    assert await store.delete_owner("legacy-owner") is True
    assert "mcp:merchant:draft:legacy" not in redis.values
    assert await store.count_owner("legacy-owner") == 0


@pytest.mark.asyncio
async def test_ambiguous_legacy_orphan_fails_closed_and_keeps_write_fence():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    redis.values["mcp:merchant:draft:ambiguous"] = "{}"

    assert await store.delete_owner("u1") is False
    assert "mcp:merchant:draft:ambiguous" in redis.values
    assert store._owner_fence_key("u1") in redis.values
    assert await store.put(_draft("blocked", user="u1")) is False


@pytest.mark.asyncio
async def test_foreign_current_member_pointing_to_target_draft_fails_closed():
    redis = FakeRedis()
    store = RedisDraftStore(redis=redis)
    assert await store.put(_draft("target", user="u1")) is True
    foreign_current = store._current_key("u2", "foreign", "luckin")
    redis.values[foreign_current] = "target"
    redis.sets[store._owner_key("u1")].add(foreign_current)

    assert await store.delete_owner("u1") is False
    assert "mcp:merchant:draft:target" in redis.values
    assert redis.values[foreign_current] == "target"
    assert store._owner_fence_key("u1") in redis.values


@pytest.mark.asyncio
async def test_agent_privacy_user_all_delegates_to_merchant_draft_store():
    class DraftStore:
        def __init__(self):
            self.users = []

        async def delete_owner(self, user_id):
            self.users.append(user_id)
            return True

    draft_store = DraftStore()
    agent = McpBridgeAgent(draft_store=draft_store)
    assert await agent.delete_personal_data("u1", "privacy_user_all") is True
    assert await agent.delete_personal_data("u1", "wrong_action") is False
    assert draft_store.users == ["u1"]


def test_draft_json_roundtrip_keeps_only_typed_fields():
    draft = _draft()
    restored = MerchantDraft.from_json(draft.to_json())
    assert restored == draft
    assert isinstance(restored.items[0], MerchantItem)


def _bootstrap_server(*, drift_write_schema=False, blank_write_pin=False):
    read_schema = {
        "type": "object", "properties": {"query": {"type": "string"}}}
    write_schema = {
        "type": "object", "properties": {"items": {"type": "array"}}}
    read = ToolSpec(
        name="menu", intent="merchant.menu", expose=True,
        schema_sha=schema_fingerprint(read_schema),
        required_scopes=["merchant.read"])
    write = ToolSpec(
        name="create", intent="merchant.internal.create", expose=False,
        schema_sha="" if blank_write_pin else schema_fingerprint(write_schema),
        write=True, require_confirm=True, required_scopes=["merchant.write"],
        idempotency_mode="local_at_most_once", retry_policy="never",
        timeout_outcome="uncertain", compensate_policy="terminal",
        success_predicate={"success": [True], "code": [0]})
    extra = ToolSpec(
        name="status", intent="merchant.status", expose=True,
        schema_sha=schema_fingerprint({"type": "object"}),
        required_scopes=["merchant.read"])
    workflow = WorkflowSpec(
        intent="merchant.order", handler="test",
        required_tools=["menu", "create"],
        required_scopes=["merchant.read", "merchant.write"])
    server = ServerSpec(
        id="merchant", command=[], version="1",
        tools=[read, write, extra], workflows=[workflow])
    offered_write_schema = ({"type": "object"}
                            if drift_write_schema else write_schema)
    offered = [
        {"name": "menu", "inputSchema": read_schema},
        {"name": "create", "inputSchema": offered_write_schema},
        {"name": "status", "inputSchema": {"type": "object"}},
        {"name": "server.extra", "inputSchema": {}},
    ]
    return server, offered


class _BootstrapClient:
    def __init__(self, offered):
        self.offered = offered
        self.server_info = {"name": "merchant", "version": "1"}

    async def start(self):
        return None

    async def initialize(self):
        return None

    async def list_tools(self):
        return self.offered

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_real_bootstrap_injects_only_declared_workflow_dependencies(monkeypatch):
    server, offered = _bootstrap_server()
    captured = []
    client = _BootstrapClient(offered)
    monkeypatch.setattr(agent_module, "load_servers", lambda _: [server])
    monkeypatch.setattr(
        McpBridgeAgent, "_make_client", staticmethod(lambda _: client))

    def make_workflow(self, handler, actual_server, spec, tools):
        captured.append((handler, actual_server.id, set(tools)))
        return SimpleNamespace()

    monkeypatch.setattr(McpBridgeAgent, "_make_workflow", make_workflow)
    agent = McpBridgeAgent(draft_store=object())
    await agent.bootstrap()

    assert captured == [("test", "merchant", {"menu", "create"})]
    assert "status" not in captured[0][2]
    assert "server.extra" not in captured[0][2]
    assert "merchant.order" in agent._workflow_bindings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("drift_write_schema", "blank_write_pin", "expected_reason"),
    [(True, False, "schema"), (False, True, "schema_sha")],
)
async def test_real_bootstrap_rejects_drifted_or_unpinned_workflow_dependency(
        monkeypatch, drift_write_schema, blank_write_pin, expected_reason):
    server, offered = _bootstrap_server(
        drift_write_schema=drift_write_schema,
        blank_write_pin=blank_write_pin)
    client = _BootstrapClient(offered)
    monkeypatch.setattr(agent_module, "load_servers", lambda _: [server])
    monkeypatch.setattr(
        McpBridgeAgent, "_make_client", staticmethod(lambda _: client))
    monkeypatch.setattr(
        McpBridgeAgent, "_make_workflow",
        lambda *_args, **_kwargs: pytest.fail("rejected workflow was initialized"))
    agent = McpBridgeAgent(draft_store=object())

    await agent.bootstrap()

    assert "merchant.order" not in agent._workflow_bindings
    assert any(expected_reason in reason for reason in agent.rejections)


def test_workflow_capability_requires_every_internal_tool():
    class _Workflow(MerchantWorkflow):
        async def prepare(self, intent, ctx, meta):
            raise AssertionError

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    agent = McpBridgeAgent(draft_store=object())
    server = type("Server", (), {"id": "merchant", "demo": False})()
    spec = WorkflowSpec(
        intent="luckin.order", handler="luckin",
        required_tools=["shop", "create"], slots=["item_query"],
        description="瑞幸下单", examples=["点一杯瑞幸"])
    # Agent 只注册已经由 bootstrap 判定依赖齐全的 workflow binding。
    from agents.mcp_bridge.src.agent import _WorkflowBinding
    agent._workflow_bindings[spec.intent] = _WorkflowBinding(server, spec, _Workflow())
    agent._sync_capabilities()
    assert [c.intent for c in agent.manifest.capabilities] == [
        "luckin.order", "shop.preview_discard",
    ]
    assert list(agent.manifest.capabilities[0].slots) == ["item_query"]


@pytest.mark.asyncio
async def test_workflow_scope_is_checked_before_prepare():
    calls = []

    class _Workflow(MerchantWorkflow):
        async def prepare(self, intent, ctx, meta):
            calls.append("prepare")
            from agents._sdk import AgentResult
            return AgentResult(speech="ok")

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    agent = McpBridgeAgent(draft_store=object())
    server = type("Server", (), {"id": "merchant", "demo": False})()
    spec = WorkflowSpec(intent="luckin.order", handler="luckin",
                        required_tools=[], required_scopes=["merchant.write"])
    from agents.mcp_bridge.src.agent import _WorkflowBinding
    agent._workflow_bindings[spec.intent] = _WorkflowBinding(server, spec, _Workflow())
    intent = type("Intent", (), {"name": "luckin.order", "slots": {}})()
    denied = await agent.handle(intent, object(), {})
    assert "缺少商户授权" in denied.speech and calls == []
    allowed = await agent.handle(
        intent, object(), {"granted_scopes": "merchant.read,merchant.write"})
    assert allowed.speech == "ok" and calls == ["prepare"]


@pytest.mark.asyncio
async def test_confirmed_cancel_is_never_dispatched_to_create_confirm():
    calls = []

    class _Workflow(MerchantWorkflow):
        async def prepare(self, intent, ctx, meta):
            raise AssertionError("取消意图不得进入 prepare")

        async def confirm(self, intent, ctx, meta, token=""):
            calls.append("create_confirm")
            raise AssertionError("确认取消不得进入创建确认")

        async def cancel(self, intent, ctx, meta):
            calls.append(("cancel", (meta or {}).get("confirmed")))
            from agents._sdk import AgentResult
            return AgentResult(speech="cancel path")

    agent = McpBridgeAgent(draft_store=object())
    server = type("Server", (), {"id": "luckin", "demo": False})()
    spec = WorkflowSpec(
        intent="luckin.order_cancel", handler="luckin",
        required_tools=["cancelOrder"], required_scopes=["merchant.write"])
    from agents.mcp_bridge.src.agent import _WorkflowBinding
    agent._workflow_bindings[spec.intent] = _WorkflowBinding(server, spec, _Workflow())
    intent = type("Intent", (), {"name": spec.intent, "slots": {}})()

    result = await agent.handle(
        intent, object(), {"granted_scopes": "merchant.write", "confirmed": "true"})

    assert result.speech == "cancel path"
    assert calls == [("cancel", "true")]


@pytest.mark.asyncio
async def test_internal_write_never_retries_even_if_caller_forgets_write_flag():
    calls = []

    class _Client:
        healthy = True
        alive = True

        async def call_tool(self, name, arguments, *, timeout_s,
                            retry_on_session_loss=True):
            calls.append(retry_on_session_loss)
            return {"ok": True, "text": "", "data": {
                "success": True, "code": 0}}

    tool = type("Tool", (), {
        "name": "createOrder", "timeout_ms": 1000, "write": True,
        "retry_policy": "never",
        "success_predicate": {"success": [True], "code": [0]}})()
    binding = type("Binding", (), {"client": _Client(), "tool": tool})()

    class _Workflow(MerchantWorkflow):
        tools = {"createOrder": binding}

        async def prepare(self, intent, ctx, meta):
            raise AssertionError

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    await _Workflow().call_tool("createOrder", {})
    assert calls == [False]


@pytest.mark.asyncio
async def test_explicit_write_call_rejects_a_misdeclared_read_tool_before_egress():
    calls = []

    class _Client:
        healthy = True
        alive = True

        async def call_tool(self, name, arguments, *, timeout_s,
                            retry_on_session_loss=True):
            calls.append((name, retry_on_session_loss))
            return {"ok": True, "data": {}}

    tool = type("Tool", (), {
        "name": "createOrder", "timeout_ms": 1000, "write": False,
        "retry_policy": "safe", "success_predicate": {}})()
    binding = type("Binding", (), {"client": _Client(), "tool": tool})()

    class _Workflow(MerchantWorkflow):
        tools = {"createOrder": binding}

        async def prepare(self, intent, ctx, meta):
            raise AssertionError

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    with pytest.raises(RuntimeError, match="not declared as write"):
        await _Workflow().call_tool("createOrder", {}, write=True)
    assert calls == []


@pytest.mark.asyncio
async def test_explicit_read_call_rejects_a_misdeclared_write_tool_before_egress():
    calls = []

    class _Client:
        healthy = True
        alive = True

        async def call_tool(self, name, arguments, *, timeout_s,
                            retry_on_session_loss=True):
            calls.append((name, retry_on_session_loss))
            return {"ok": True, "data": {}}

    tool = type("Tool", (), {
        "name": "queryOrder", "timeout_ms": 1000, "write": True,
        "retry_policy": "never",
        "success_predicate": {"success": [True]}})()
    binding = type("Binding", (), {"client": _Client(), "tool": tool})()

    class _Workflow(MerchantWorkflow):
        tools = {"queryOrder": binding}

        async def prepare(self, intent, ctx, meta):
            raise AssertionError

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    with pytest.raises(RuntimeError, match="not declared as read"):
        await _Workflow().call_tool("queryOrder", {}, write=False)
    assert calls == []


@pytest.mark.asyncio
async def test_internal_write_executes_declared_business_predicate():
    class _Client:
        healthy = True
        alive = True

        async def call_tool(self, name, arguments, *, timeout_s,
                            retry_on_session_loss=True):
            return {"ok": True, "text": "", "data": {
                "success": False, "code": 500}}

    tool = type("Tool", (), {
        "name": "createOrder", "timeout_ms": 1000, "write": True,
        "retry_policy": "never",
        "success_predicate": {"success": [True], "code": [0]}})()
    binding = type("Binding", (), {"client": _Client(), "tool": tool})()

    class _Workflow(MerchantWorkflow):
        tools = {"createOrder": binding}

        async def prepare(self, intent, ctx, meta):
            raise AssertionError

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    with pytest.raises(DeclaredBusinessRejected):
        await _Workflow().call_tool("createOrder", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [
    {"success": True},
    {"success": "true", "code": 0},
    {"success": True, "code": "0"},
])
async def test_internal_write_incomplete_or_typed_drift_is_not_rejection(
        envelope):
    class _Client:
        healthy = True
        alive = True

        async def call_tool(self, name, arguments, *, timeout_s,
                            retry_on_session_loss=True):
            return {"ok": True, "text": "", "data": envelope}

    tool = type("Tool", (), {
        "name": "createOrder", "timeout_ms": 1000, "write": True,
        "retry_policy": "never",
        "success_predicate": {"success": [True], "code": [0]}})()
    binding = type("Binding", (), {"client": _Client(), "tool": tool})()

    class _Workflow(MerchantWorkflow):
        tools = {"createOrder": binding}

        async def prepare(self, intent, ctx, meta):
            raise AssertionError

        async def confirm(self, intent, ctx, meta, token=""):
            raise AssertionError

    with pytest.raises(DeclaredBusinessIncomplete):
        await _Workflow().call_tool("createOrder", {}, write=True)


# ── P3（EVA 遗留卡）：数量槽容错解析 ──

import pytest as _pytest
from agents.mcp_bridge.src.merchant.base import parse_quantity


@_pytest.mark.parametrize("raw,expect", [
    ("1", 1), ("20", 20), ("一杯", 1), ("两份", 2), ("三个", 3),
    ("十二杯", 12), ("２份", 2), ("１２", 12), ("2杯", 2), ("二十", 20),
])
def test_parse_quantity_tolerant(raw, expect):
    """「一杯」「2份」「１２」是 planner 填槽的自然形态——解析尽量宽。"""
    assert parse_quantity(raw) == expect


@_pytest.mark.parametrize("raw", ["0", "21", "两百", "很多", "杯", "", "  ", "abc",
                                  "1.5", "-3", "二十一"])
def test_parse_quantity_bounds_not_loosened(raw):
    """校验边界不松：仍 1–20 整数，解析不出/越界一律 None。"""
    assert parse_quantity(raw) is None
