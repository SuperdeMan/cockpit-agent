"""Redis-backed 一次性商户订单草稿；Redis 不可用时写能力 fail-closed。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
from dataclasses import asdict
from contextlib import asynccontextmanager

from .models import MerchantDraft

logger = logging.getLogger("agent.mcp_bridge.merchant.drafts")

_DRAFT_PREFIX = "mcp:merchant:draft:"
_CURRENT_PREFIX = "mcp:merchant:current:"
_OWNER_PREFIX = "mcp:merchant:owner:"
_OWNER_META_SUFFIX = ":meta"
_OWNER_FENCE_SUFFIX = ":deleting"
_OWNER_ACTIVE_SUFFIX = ":active"
_OWNER_INDEX_VERSION = "1"
_PRIVACY_TOMBSTONE = "privacy_deleted"

_PUT_LUA = r"""
-- merchant-draft-put-v3
if redis.call('EXISTS', KEYS[5]) == 1 then return 0 end
local active = redis.call('GET', KEYS[6])
if ARGV[5] ~= '' then
  if active ~= ARGV[5] then return 0 end
elseif active then
  return 0
end
local cardinality = redis.call('SCARD', KEYS[3])
local version = redis.call('HGET', KEYS[4], 'version')
if version then
  local state = redis.call('HGET', KEYS[4], 'state')
  local expected = tonumber(redis.call('HGET', KEYS[4], 'count') or '-1')
  if version ~= ARGV[4] or expected ~= cardinality then return 0 end
  if state ~= 'active' and not (state == 'idle' and cardinality == 0) then
    return 0
  end
elseif cardinality ~= 0
   or redis.call('EXISTS', KEYS[1]) == 1
   or redis.call('EXISTS', KEYS[2]) == 1 then
  return 0
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SADD', KEYS[3], KEYS[1], KEYS[2])
local new_count = redis.call('SCARD', KEYS[3])
redis.call('HSET', KEYS[4], 'version', ARGV[4], 'state', 'active',
           'count', new_count)
redis.call('EXPIRE', KEYS[3], ARGV[3])
redis.call('EXPIRE', KEYS[4], ARGV[3])
return 1
"""

# 单个 Lua 脚本同时验证 owner/session/merchant/operation/current pointer
# 并删除。operation 必须在 DEL 前比对，否则一条 cancel 确认可以先吃掉
# create 草稿（或反过来），即使 Python 层事后拒绝也无法恢复。
_CONSUME_LUA = r"""
-- merchant-draft-consume-v3
if redis.call('EXISTS', KEYS[5]) == 1 then return '' end
local requires_lease = ARGV[5] == 'create' or ARGV[5] == 'cancel'
if requires_lease and redis.call('EXISTS', KEYS[6]) == 1 then return '' end
local cardinality = redis.call('SCARD', KEYS[3])
local version = redis.call('HGET', KEYS[4], 'version')
local state = redis.call('HGET', KEYS[4], 'state')
local expected = tonumber(redis.call('HGET', KEYS[4], 'count') or '-1')
if version ~= ARGV[7] or state ~= 'active' or expected ~= cardinality
   or redis.call('SISMEMBER', KEYS[3], KEYS[1]) ~= 1
   or redis.call('SISMEMBER', KEYS[3], KEYS[2]) ~= 1 then return '' end
local raw = redis.call('GET', KEYS[1])
if not raw then return '' end
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return '' end
local value = cjson.decode(raw)
if value['owner_digest'] ~= ARGV[2] or value['session_digest'] ~= ARGV[3]
   or value['merchant'] ~= ARGV[4] or value['operation'] ~= ARGV[5]
   then return '' end
if requires_lease then
  local leased = redis.call('SET', KEYS[6], ARGV[1], 'EX', ARGV[6], 'NX')
  if not leased then return '' end
end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
local removed = redis.call('SREM', KEYS[3], KEYS[1], KEYS[2])
if removed ~= 2 then return '' end
local new_count = redis.call('SCARD', KEYS[3])
if new_count == 0 then
  redis.call('DEL', KEYS[3])
  redis.call('HSET', KEYS[4], 'version', ARGV[7], 'state', 'idle',
             'count', 0)
  redis.call('EXPIRE', KEYS[4], ARGV[6])
else
  redis.call('HSET', KEYS[4], 'count', new_count)
end
return raw
"""

_DELETE_OWNER_LUA = r"""
-- merchant-draft-delete-owner-v3
if redis.call('EXISTS', KEYS[3]) == 1 then return {-2, 0, 0} end
local cardinality = redis.call('SCARD', KEYS[1])
local version = redis.call('HGET', KEYS[2], 'version')
local state = redis.call('HGET', KEYS[2], 'state')
local expected = tonumber(redis.call('HGET', KEYS[2], 'count') or '-1')
if version ~= ARGV[5] then return {-1, 0, 0} end
if state == 'privacy_deleted' and expected == 0 and cardinality == 0 then
  return {1, 0, 0}
end
if state == 'idle' and expected == 0 and cardinality == 0 then
  redis.call('HSET', KEYS[2], 'version', ARGV[5],
             'state', 'privacy_deleted', 'count', 0)
  redis.call('EXPIRE', KEYS[2], ARGV[4])
  return {1, 0, 0}
end
if state ~= 'active' or cardinality == 0 or expected ~= cardinality then
  return {-1, 0, 0}
end
local members = redis.call('SMEMBERS', KEYS[1])
local live = 0
for _, key in ipairs(members) do
  if string.sub(key, 1, string.len(ARGV[1])) == ARGV[1] then
    local raw = redis.call('GET', key)
    if raw then
      local ok, value = pcall(cjson.decode, raw)
      if not ok or type(value) ~= 'table'
         or value['owner_digest'] ~= ARGV[3] then
        return {-1, 0, 0}
      end
      live = live + 1
    end
  elseif string.sub(key, 1, string.len(ARGV[2])) == ARGV[2] then
    local token = redis.call('GET', key)
    if token then
      local draft_key = ARGV[1] .. token
      if redis.call('SISMEMBER', KEYS[1], draft_key) ~= 1 then
        return {-1, 0, 0}
      end
      local raw = redis.call('GET', draft_key)
      if not raw then return {-1, 0, 0} end
      local ok, value = pcall(cjson.decode, raw)
      if not ok or type(value) ~= 'table'
         or value['owner_digest'] ~= ARGV[3] then
        return {-1, 0, 0}
      end
      local canonical = ARGV[2] .. ARGV[3] .. ':'
                        .. tostring(value['session_digest']) .. ':'
                        .. string.lower(tostring(value['merchant']))
      if key ~= canonical then return {-1, 0, 0} end
      live = live + 1
    end
  else
    return {-1, 0, 0}
  end
end
local removed = 0
for _, key in ipairs(members) do
  removed = removed + redis.call('DEL', key)
end
if removed ~= live then return {-1, removed, live} end
redis.call('DEL', KEYS[1])
redis.call('HSET', KEYS[2], 'version', ARGV[5], 'state', 'privacy_deleted',
           'count', 0)
redis.call('EXPIRE', KEYS[2], ARGV[4])
return {1, removed, live}
"""

_COUNT_OWNER_LUA = r"""
-- merchant-draft-count-owner-v2
local cardinality = redis.call('SCARD', KEYS[1])
local version = redis.call('HGET', KEYS[2], 'version')
local state = redis.call('HGET', KEYS[2], 'state')
local expected = tonumber(redis.call('HGET', KEYS[2], 'count') or '-1')
if version ~= ARGV[4] then return {-1, 0} end
if (state == 'idle' or state == 'privacy_deleted')
   and expected == 0 and cardinality == 0 then
  return {1, 0}
end
if state ~= 'active' or cardinality == 0 or expected ~= cardinality then
  return {-1, 0}
end
local members = redis.call('SMEMBERS', KEYS[1])
local live = 0
for _, key in ipairs(members) do
  if string.sub(key, 1, string.len(ARGV[1])) == ARGV[1] then
    local raw = redis.call('GET', key)
    if raw then
      local ok, value = pcall(cjson.decode, raw)
      if not ok or type(value) ~= 'table'
         or value['owner_digest'] ~= ARGV[3] then return {-1, 0} end
      live = live + 1
    end
  elseif string.sub(key, 1, string.len(ARGV[2])) == ARGV[2] then
    local token = redis.call('GET', key)
    if token then
      local draft_key = ARGV[1] .. token
      if redis.call('SISMEMBER', KEYS[1], draft_key) ~= 1 then
        return {-1, 0}
      end
      local raw = redis.call('GET', draft_key)
      if not raw then return {-1, 0} end
      local ok, value = pcall(cjson.decode, raw)
      if not ok or type(value) ~= 'table'
         or value['owner_digest'] ~= ARGV[3] then return {-1, 0} end
      local canonical = ARGV[2] .. ARGV[3] .. ':'
                        .. tostring(value['session_digest']) .. ':'
                        .. string.lower(tostring(value['merchant']))
      if key ~= canonical then return {-1, 0} end
      live = live + 1
    end
  else
    return {-1, 0}
  end
end
return {1, live}
"""

_PERSIST_FENCE_LUA = r"""
if redis.call('GET', KEYS[1]) == ARGV[1]
   and redis.call('EXISTS', KEYS[2]) == 0 then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""

_AUTHORIZE_LEASE_LUA = r"""
-- merchant-draft-authorize-lease-v1
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('GET', KEYS[2]) == ARGV[2] then return 0 end
redis.call('EXPIRE', KEYS[1], ARGV[3])
if redis.call('EXISTS', KEYS[2]) == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[3])
end
return 1
"""

_RELEASE_LEASE_LUA = r"""
-- merchant-draft-release-lease-v1
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:24]


class RedisDraftStore:
    def __init__(self, *, redis=None, redis_url: str = "", ttl_seconds: int = 600):
        self._r = redis
        self._url = redis_url or os.getenv("REDIS_URL", "")
        self.ttl_seconds = max(30, int(ttl_seconds or 600))
        self._renew_interval_seconds = max(
            0.25, min(5.0, self.ttl_seconds / 4.0))

    async def _redis(self):
        if self._r is not None:
            return self._r
        if not self._url:
            return None
        try:
            import redis.asyncio as aioredis
            candidate = aioredis.from_url(
                self._url, decode_responses=True, socket_timeout=3,
                socket_connect_timeout=3, health_check_interval=30,
                retry_on_timeout=False)
            await candidate.ping()
            self._r = candidate
            return self._r
        except Exception as exc:
            logger.warning("MerchantDraftStore Redis 不可用，写能力拒绝：%s",
                           type(exc).__name__)
            return None

    @staticmethod
    def _current_key(user_id: str, session_id: str, merchant: str) -> str:
        return (f"{_CURRENT_PREFIX}{_digest(user_id)}:{_digest(session_id)}:"
                f"{str(merchant or '').strip().lower()}")

    @staticmethod
    def _owner_key(user_id: str) -> str:
        return _OWNER_PREFIX + _digest(user_id)

    @classmethod
    def _owner_meta_key(cls, user_id: str) -> str:
        return cls._owner_key(user_id) + _OWNER_META_SUFFIX

    @classmethod
    def _owner_fence_key(cls, user_id: str) -> str:
        return cls._owner_key(user_id) + _OWNER_FENCE_SUFFIX

    @classmethod
    def _owner_active_key(cls, user_id: str) -> str:
        return cls._owner_key(user_id) + _OWNER_ACTIVE_SUFFIX

    async def put(self, draft: MerchantDraft, *, lease_token: str = "") -> bool:
        r = await self._redis()
        operation = str(draft.operation or "").strip().lower()
        if (r is None or not draft.token or not draft.user_id or
                not draft.session_id or not operation or
                operation != str(draft.operation)):
            return False
        owner_digest = _digest(draft.user_id)
        session_digest = _digest(draft.session_id)
        # Redis value 不存 user/session 明文；业务草稿本体去掉这两个字段。
        payload = asdict(draft)
        payload.pop("token", None)
        payload.pop("user_id", None)
        payload.pop("session_id", None)
        payload["owner_digest"] = owner_digest
        payload["session_digest"] = session_digest
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                         sort_keys=True, allow_nan=False)
        draft_key = _DRAFT_PREFIX + draft.token
        current_key = self._current_key(
            draft.user_id, draft.session_id, draft.merchant)
        owner_key = self._owner_key(draft.user_id)
        owner_meta_key = self._owner_meta_key(draft.user_id)
        owner_fence_key = self._owner_fence_key(draft.user_id)
        owner_active_key = self._owner_active_key(draft.user_id)
        try:
            stored = await r.eval(
                _PUT_LUA, 6, draft_key, current_key, owner_key, owner_meta_key,
                owner_fence_key, owner_active_key, raw, draft.token,
                self.ttl_seconds,
                _OWNER_INDEX_VERSION, str(lease_token or ""))
            return stored == 1
        except Exception as exc:
            logger.warning("MerchantDraftStore.put 失败，写能力拒绝：%s",
                           type(exc).__name__)
            return False

    async def consume(self, token: str, *, user_id: str, session_id: str,
                      merchant: str, expected_action: str) -> MerchantDraft | None:
        expected_action = str(expected_action or "").strip().lower()
        if (not token or not user_id or not session_id or not merchant or
                not expected_action):
            return None
        r = await self._redis()
        if r is None:
            return None
        owner_digest = _digest(user_id)
        session_digest = _digest(session_id)
        current_key = self._current_key(user_id, session_id, merchant)
        owner_key = self._owner_key(user_id)
        owner_meta_key = self._owner_meta_key(user_id)
        owner_fence_key = self._owner_fence_key(user_id)
        owner_active_key = self._owner_active_key(user_id)
        try:
            raw = await r.eval(
                _CONSUME_LUA, 6, _DRAFT_PREFIX + token, current_key, owner_key,
                owner_meta_key, owner_fence_key, owner_active_key, token,
                owner_digest, session_digest, merchant, expected_action,
                self.ttl_seconds, _OWNER_INDEX_VERSION)
        except Exception as exc:
            logger.warning("MerchantDraftStore.consume 失败，写能力拒绝：%s",
                           type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            payload.pop("owner_digest", None)
            payload.pop("session_digest", None)
            payload["token"] = token
            payload["user_id"] = user_id
            payload["session_id"] = session_id
            return MerchantDraft.from_json(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    async def authorize(self, token: str, *, user_id: str) -> bool:
        """Prove that this owner/token still holds the write-operation lease."""
        if not token or not user_id:
            return False
        r = await self._redis()
        if r is None:
            return False
        try:
            allowed = await r.eval(
                _AUTHORIZE_LEASE_LUA, 2, self._owner_active_key(user_id),
                self._owner_fence_key(user_id), token, _PRIVACY_TOMBSTONE,
                self.ttl_seconds)
            return int(allowed or 0) == 1
        except Exception as exc:
            logger.warning("MerchantDraftStore.authorize failed: %s",
                           type(exc).__name__)
            return False

    async def release(self, token: str, *, user_id: str) -> bool:
        """Release only the exact owner/token lease; stale releases are inert."""
        if not token or not user_id:
            return False
        r = await self._redis()
        if r is None:
            return False
        try:
            released = await r.eval(
                _RELEASE_LEASE_LUA, 1, self._owner_active_key(user_id), token)
            return int(released or 0) == 1
        except Exception as exc:
            logger.warning("MerchantDraftStore.release failed: %s",
                           type(exc).__name__)
            return False

    @asynccontextmanager
    async def operation_hold(self, token: str, *, user_id: str):
        """Keep one active operation lease alive until every effect settles."""
        if not await self.authorize(token, user_id=user_id):
            yield False
            return
        stop = asyncio.Event()
        lost = asyncio.Event()
        owner_task = asyncio.current_task()

        async def renew():
            while True:
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._renew_interval_seconds)
                    return
                except asyncio.TimeoutError:
                    if not await self.authorize(token, user_id=user_id):
                        lost.set()
                        if owner_task is not None and not owner_task.done():
                            owner_task.cancel()
                        return

        heartbeat = asyncio.create_task(renew())
        try:
            yield not lost.is_set()
        finally:
            stop.set()
            try:
                await asyncio.shield(heartbeat)
            except asyncio.CancelledError:
                await heartbeat
                raise
            await asyncio.shield(self.release(token, user_id=user_id))

    async def consume_current(self, *, user_id: str, session_id: str,
                              merchant: str,
                              expected_action: str) -> MerchantDraft | None:
        r = await self._redis()
        if r is None:
            return None
        try:
            token = await r.get(self._current_key(user_id, session_id, merchant))
        except Exception as exc:
            logger.warning("MerchantDraftStore current 读取失败：%s", type(exc).__name__)
            return None
        return await self.consume(str(token or ""), user_id=user_id,
                                  session_id=session_id, merchant=merchant,
                                  expected_action=expected_action)

    async def discard(self, token: str, *, user_id: str, session_id: str,
                      merchant: str, expected_action: str) -> bool:
        """消费并丢弃草稿；用于上游调用前确定性校验失败后的安全清理。"""
        draft = await self.consume(
            token, user_id=user_id, session_id=session_id,
            merchant=merchant, expected_action=expected_action)
        if draft is None:
            return False
        if draft.operation in {"create", "cancel"}:
            return await self.release(draft.token, user_id=draft.user_id)
        return True

    async def count_owner(self, user_id: str) -> int:
        """Count live draft/current keys for one user without exposing values."""
        if not user_id:
            return 0
        r = await self._redis()
        if r is None:
            return -1
        try:
            result = await r.eval(
                _COUNT_OWNER_LUA, 2, self._owner_key(user_id),
                self._owner_meta_key(user_id), _DRAFT_PREFIX,
                _CURRENT_PREFIX, _digest(user_id), _OWNER_INDEX_VERSION)
            if (not isinstance(result, (list, tuple)) or len(result) != 2
                    or int(result[0]) != 1):
                return -1
            return int(result[1])
        except Exception as exc:
            logger.warning("MerchantDraftStore.count_owner 失败：%s",
                           type(exc).__name__)
            return -1

    async def delete_owner(self, user_id: str) -> bool:
        """Delete owner data behind a write fence and prove the result."""
        if not user_id:
            return False
        r = await self._redis()
        if r is None:
            return False
        owner_key = self._owner_key(user_id)
        owner_meta_key = self._owner_meta_key(user_id)
        owner_fence_key = self._owner_fence_key(user_id)
        owner_active_key = self._owner_active_key(user_id)
        owner_digest = _digest(user_id)
        fence_token = secrets.token_hex(16)
        try:
            existing_fence = str(await r.get(owner_fence_key) or "")
            if existing_fence == _PRIVACY_TOMBSTONE:
                # Successful deletion is idempotent for the whole safety TTL.
                # Re-run the proof in case a legacy/orphan key was injected.
                return await self._recover_delete_owner(
                    r, owner_digest=owner_digest, owner_key=owner_key,
                    owner_meta_key=owner_meta_key)
            if existing_fence:
                # A previous request may have left a pending fence while an
                # operation lease was active.  Reuse that opaque fence token;
                # never clear or replace it speculatively.
                fence_token = existing_fence
            else:
                fenced = await r.set(
                    owner_fence_key, fence_token, ex=self.ttl_seconds, nx=True)
                if not fenced:
                    return False
            # Fence first: consume checks the same key in its Lua transaction,
            # so either the lease or deletion wins, never both.
            if await r.exists(owner_active_key):
                return False
            # The indexed Lua path is atomic and fast.  A fenced cursor scan is
            # still mandatory: it proves there is no pre-marker/legacy orphan.
            await self._delete_owner_indexed(user_id)
            proven = await self._recover_delete_owner(
                r, owner_digest=owner_digest, owner_key=owner_key,
                owner_meta_key=owner_meta_key)
            if not proven:
                return False
            persisted = await r.eval(
                _PERSIST_FENCE_LUA, 2, owner_fence_key, owner_active_key,
                fence_token,
                _PRIVACY_TOMBSTONE, self.ttl_seconds)
            return int(persisted or 0) == 1
        except Exception as exc:
            logger.warning("MerchantDraftStore.delete_owner failed: %s",
                           type(exc).__name__)
            return False

    @staticmethod
    async def _scan_keys(r, pattern: str):
        """Yield keys through bounded cursor pages; privacy cleanup only."""
        cursor: int | str = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match=pattern, count=128)
            for key in keys:
                yield str(key)
            if int(cursor) == 0:
                break

    async def _recover_delete_owner(
            self, r, *, owner_digest: str, owner_key: str,
            owner_meta_key: str) -> bool:
        """Fenced repair for legacy/orphan state without trusting the index."""
        try:
            if await r.exists(owner_key + _OWNER_ACTIVE_SUFFIX):
                return False
            members = {str(key) for key in await r.smembers(owner_key)}
            for key in members:
                if key.startswith(_DRAFT_PREFIX):
                    raw = await r.get(key)
                    if not raw:
                        continue
                    try:
                        indexed = json.loads(raw)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return False
                    if not indexed.get("owner_digest"):
                        return False
                    # Foreign membership is not authority to delete its value.
                    if indexed.get("owner_digest") != owner_digest:
                        continue
                elif key.startswith(_CURRENT_PREFIX):
                    token = str(await r.get(key) or "")
                    if not token:
                        continue
                    raw = await r.get(_DRAFT_PREFIX + token)
                    if raw:
                        try:
                            indexed = json.loads(raw)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            return False
                        if not indexed.get("owner_digest"):
                            return False
                        # Never follow a poisoned pointer into a foreign draft.
                        if indexed.get("owner_digest") != owner_digest:
                            continue
                        canonical = (
                            f"{_CURRENT_PREFIX}{owner_digest}:"
                            f"{indexed.get('session_digest')}:"
                            f"{str(indexed.get('merchant') or '').lower()}")
                        if key != canonical:
                            return False

            target_drafts: list[str] = []
            async for key in self._scan_keys(r, _DRAFT_PREFIX + "*"):
                raw = await r.get(key)
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return False
                if not value.get("owner_digest"):
                    return False
                if value.get("owner_digest") == owner_digest:
                    target_drafts.append(key)

            target_currents = [
                key async for key in self._scan_keys(
                    r, f"{_CURRENT_PREFIX}{owner_digest}:*")
            ]
            if target_drafts:
                await r.delete(*target_drafts)
            if target_currents:
                await r.delete(*target_currents)
            await r.delete(owner_key)
            await r.hset(
                owner_meta_key,
                mapping={"version": _OWNER_INDEX_VERSION,
                         "state": _PRIVACY_TOMBSTONE, "count": 0})
            await r.expire(owner_meta_key, self.ttl_seconds)

            async for key in self._scan_keys(r, _DRAFT_PREFIX + "*"):
                raw = await r.get(key)
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return False
                if not value.get("owner_digest"):
                    return False
                if value.get("owner_digest") == owner_digest:
                    return False
            async for _ in self._scan_keys(
                    r, f"{_CURRENT_PREFIX}{owner_digest}:*"):
                return False
            if await r.exists(owner_key):
                return False
            return (str(await r.hget(owner_meta_key, "version") or "")
                    == _OWNER_INDEX_VERSION
                    and str(await r.hget(owner_meta_key, "state") or "")
                    == _PRIVACY_TOMBSTONE
                    and int(await r.hget(owner_meta_key, "count") or -1) == 0)
        except Exception:
            return False

    async def _delete_owner_indexed(self, user_id: str) -> bool:
        """Atomically delete every merchant draft/current pointer for a user."""
        if not user_id:
            return False
        r = await self._redis()
        if r is None:
            return False
        try:
            result = await r.eval(
                _DELETE_OWNER_LUA, 3, self._owner_key(user_id),
                self._owner_meta_key(user_id), self._owner_active_key(user_id),
                _DRAFT_PREFIX,
                _CURRENT_PREFIX, _digest(user_id), self.ttl_seconds,
                _OWNER_INDEX_VERSION)
            if not isinstance(result, (list, tuple)) or len(result) != 3:
                return False
            status, removed, live = (int(value) for value in result)
            return status == 1 and removed == live
        except Exception as exc:
            logger.warning("MerchantDraftStore.delete_owner 失败：%s",
                           type(exc).__name__)
            return False
