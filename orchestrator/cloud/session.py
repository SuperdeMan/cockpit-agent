"""SessionStore：多轮会话状态（待确认/待补槽），Redis 持久。

WS3 §6。支持 confirm/slot 续接 + TTL 超时作废。
"""
from __future__ import annotations
import json
import time
import logging
import os
import hashlib
from dataclasses import asdict

from .models import SessionState, Plan, Step, StepResult, StepStatus

logger = logging.getLogger("planner.session")

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

_KEY_PREFIX = "planner:sess:"
_OWNER_PREFIX = "planner:sess-owner:"
_OWNER_FENCE_PREFIX = "planner:sess-owner-fence:"
_OWNER_FENCE_TTL = 300
_DELETING = "deleting"
_DELETED = "deleted"
_DEFAULT_TTL = 300  # 秒（确认/补槽挂起态；行程等慢流程每轮数十秒+用户阅读，90s 太短致确认过期）
# 小容量挂起表（QA 卡 Q1-C）：单槽 `_suspend` 覆盖旧挂起的语义，注释里的理由是
# 「确认条 UI 也只有一个」——**两个并行任务下就不成立**（I-051 商户补槽跨域劫持、
# I-037① 无订单却进退款确认）。上限刻意小：挂起是**用户脑子里记得的东西**，
# 三条已经是人能同时惦记的上限，再多只是把「猜错哪一条」换成「猜错更多条」。
_PENDING_CAPACITY = 3
# 焦点态：与挂起态分开存（每轮持久、完成不清，供跨轮指代消解）。TTL 比挂起态长。
_FOCUS_PREFIX = "planner:focus:"
_FOCUS_TTL = 300  # 秒

PERSONAL_DATA_TARGETS = (
    {
        "id": "planner_pending_session",
        "storage_variants": (
            "planner:sess:*",
            "planner:sess-owner:*",
            "planner:sess-owner-fence:*",
            "planner:focus:*",
        ),
    },
)

_SAVE_OWNER_LUA = r"""
if redis.call('EXISTS', KEYS[3]) == 1 then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
redis.call('SADD', KEYS[2], KEYS[1])
local current_ttl = redis.call('TTL', KEYS[2])
if current_ttl < tonumber(ARGV[2]) then
  redis.call('EXPIRE', KEYS[2], ARGV[2])
end
return 1
"""

_SAVE_FOCUS_LUA = r"""
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""

_LOAD_OWNER_LUA = r"""
if redis.call('EXISTS', KEYS[2]) == 1 then return false end
return redis.call('GET', KEYS[1])
"""


class SessionStore:
    def __init__(self, redis_url: str = ""):
        self._url = redis_url or os.getenv("REDIS_URL", "")
        self._r = None
        # Keys include owner and session digests.  A caller-controlled
        # session_id can never overwrite or address another owner's state.
        self._mem: dict[str, tuple[SessionState, float]] = {}
        self._focus_mem: dict[str, tuple[dict, float]] = {}
        # owner -> expiry; successful privacy deletion retains this tombstone
        # for at least the pending TTL to reject requests already in flight.
        self._owner_fences: dict[str, float] = {}

    async def _redis(self):
        if aioredis and self._url and self._r is None:
            try:
                self._r = aioredis.from_url(
                    self._url, decode_responses=True, socket_timeout=3,
                    socket_connect_timeout=3, socket_keepalive=True,
                    health_check_interval=30, retry_on_timeout=True)
                await self._r.ping()
            except Exception as e:
                logger.warning("Redis unavailable; configured store fails closed: %s", e)
                self._r = None
        return self._r

    async def shared_backend_ready(self) -> bool:
        """Whether this store can prove a cross-replica privacy deletion.

        Process-local memory is valid for ordinary PoC continuation, but a
        production privacy responder must not ACK from it: another replica
        could still retain the same owner.  Callers use this read-only probe
        before installing/responding on the shared delete bus.
        """
        if not self._url:
            return False
        r = await self._redis()
        if r is None:
            return False
        try:
            return bool(await r.ping())
        except Exception as exc:
            logger.warning(
                "SessionStore shared backend unavailable: %s",
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(
            str(value or "").encode("utf-8")
        ).hexdigest()[:24]

    @classmethod
    def _session_key(cls, user_id: str, session_id: str) -> str:
        return f"{_KEY_PREFIX}{cls._digest(user_id)}:{cls._digest(session_id)}"

    @classmethod
    def _focus_key(cls, user_id: str, session_id: str) -> str:
        return f"{_FOCUS_PREFIX}{cls._digest(user_id)}:{cls._digest(session_id)}"

    def _memory_tombstoned(self, user_id: str) -> bool:
        expires = self._owner_fences.get(user_id, 0.0)
        if expires > time.time():
            return True
        self._owner_fences.pop(user_id, None)
        return False

    async def load(self, session_id: str, *,
                   owner_user_id: str = "",
                   operation_id: str = "") -> SessionState | None:
        """加载挂起的会话状态。TTL 过期返回 None。

        `operation_id` 非空 = 按寻址键定位（QA 卡 Q1-B）：**对不上就返回 None，
        绝不回落到「最近一条」**——静默回落正是 I-013「全局确认命中旧请求」
        那个缺陷本身（同 B3「认不出就用默认值」）。
        """
        owner = str(owner_user_id or "").strip()
        if not owner or not str(session_id or "").strip():
            return None
        entries = await self.load_all(session_id, owner_user_id=owner)
        wanted = str(operation_id or "").strip()
        if wanted:
            return next(
                (s for s in entries if s.operation_id == wanted), None)
        return entries[-1] if entries else None

    @staticmethod
    def _decode(raw, owner: str) -> list[SessionState]:
        """反序列化挂起表，顺序 = 挂起先后（最后一条最新）。

        兼容上一版部署留下的**单对象**负载（滚动升级窗口里同一个 Redis 会同时
        存在两种形状）——认不出的一律当空，绝不半解析出一个残缺状态。
        """
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        items = data if isinstance(data, list) else [data]
        out: list[SessionState] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("owner_user_id") or "").strip() != owner:
                continue
            try:
                out.append(SessionState(**item))
            except TypeError:
                continue        # 未知字段 = 更新的写入方，这一条读不了就跳过
        return out

    @staticmethod
    def _live(entries: list[SessionState]) -> list[SessionState]:
        """逐条按自己的截止时刻过期（0 = 旧数据，不判过期，交给 key TTL）。"""
        now = time.time()
        return [s for s in entries
                if not s.expires_at or s.expires_at > now]

    async def load_all(self, session_id: str, *,
                       owner_user_id: str = "") -> list[SessionState]:
        """本会话全部未过期挂起，顺序 = 挂起先后（最后一条最新）。"""
        owner = str(owner_user_id or "").strip()
        if not owner or not str(session_id or "").strip():
            return []
        r = await self._redis()
        if self._url and r is None:
            return []
        key = self._session_key(owner, session_id)
        if r:
            raw = await r.eval(
                _LOAD_OWNER_LUA, 2, key, self._owner_fence_key(owner))
            return self._live(self._decode(raw, owner)) if raw else []

        # 内存兜底
        if self._memory_tombstoned(owner):
            return []
        entry = self._mem.get(key)
        if not entry:
            return []
        entries, expire_ts = entry
        if time.time() >= expire_ts:
            del self._mem[key]
            return []
        return self._live([s for s in entries
                           if str(s.owner_user_id or "").strip() == owner])

    async def save(self, session_id: str, state: SessionState) -> bool:
        """Save owner-bound pending state, or fail closed."""
        ok, _evicted = await self.save_pending(session_id, state)
        return ok

    async def save_pending(
            self, session_id: str,
            state: SessionState) -> tuple[bool, SessionState | None]:
        """存一条挂起，返回 `(是否成功, 被 LRU 淘汰的那条或 None)`。

        **淘汰必须回传**：调用方要拿它对用户说一句「刚才那条 X 已过期」——
        静默丢弃就是 B3 那条「认不出就用默认值」的确认版（卡 §3-Q1 的 ⚠）。
        同 `operation_id` 视为**替换**（补槽再次追问不占新槽位）。
        """
        owner = str(state.owner_user_id or "").strip()
        if not owner or not str(session_id or "").strip():
            return False, None
        r = await self._redis()
        if self._url and r is None:
            return False, None
        key = self._session_key(owner, session_id)
        ttl = state.ttl_seconds or _DEFAULT_TTL
        if not state.expires_at:
            state.expires_at = time.time() + ttl

        entries = await self.load_all(session_id, owner_user_id=owner)
        entries = [s for s in entries
                   if s.operation_id != state.operation_id]
        entries.append(state)
        evicted: SessionState | None = None
        while len(entries) > _PENDING_CAPACITY:
            evicted = entries.pop(0)

        if not await self._write(r, key, owner, entries):
            return False, None
        return True, evicted

    async def _write(self, r, key: str, owner: str,
                     entries: list[SessionState]) -> bool:
        """整表落盘。key TTL 取各条剩余寿命的最大值（逐条过期在读侧兜住）。"""
        if not entries:
            if r:
                pipe = r.pipeline(transaction=True)
                pipe.delete(key)
                pipe.srem(self._owner_key(owner), key)
                await pipe.execute()
            else:
                self._mem.pop(key, None)
            return True
        now = time.time()
        ttl = max(int(s.expires_at - now) for s in entries)
        ttl = max(ttl, 1)
        if r:
            data = json.dumps([asdict(s) for s in entries],
                              ensure_ascii=False, default=str)
            saved = await r.eval(
                _SAVE_OWNER_LUA, 3, key, self._owner_key(owner),
                self._owner_fence_key(owner), data, ttl)
            return int(saved or 0) == 1
        if self._memory_tombstoned(owner):
            return False
        self._mem[key] = (entries, now + ttl)
        return True

    async def clear(self, session_id: str, *, owner_user_id: str = "",
                    operation_id: str | None = None) -> bool:
        """Clear this owner's pending state without clearing focus.

        `operation_id=None` = 清空整张挂起表（隐私删除/整会话作废）；
        给了 id = **只清那一条**，其余挂起原样保留（Q1-C）。
        """
        owner = str(owner_user_id or "").strip()
        if not owner or not str(session_id or "").strip():
            return False
        r = await self._redis()
        if self._url and r is None:
            return False
        key = self._session_key(owner, session_id)
        if operation_id is None:
            if r:
                pipe = r.pipeline(transaction=True)
                pipe.delete(key)
                pipe.srem(self._owner_key(owner), key)
                result = await pipe.execute()
                return bool(result and int(result[0] or 0))
            return self._mem.pop(key, None) is not None

        entries = await self.load_all(session_id, owner_user_id=owner)
        kept = [s for s in entries if s.operation_id != operation_id]
        if len(kept) == len(entries):
            return False
        return await self._write(r, key, owner, kept)

    @staticmethod
    def _owner_key(user_id: str) -> str:
        return _OWNER_PREFIX + SessionStore._digest(user_id)

    @staticmethod
    def _owner_fence_key(user_id: str) -> str:
        return _OWNER_FENCE_PREFIX + SessionStore._digest(user_id)

    async def _scan_owner_records(self, r, user_id: str):
        """Return an attributable snapshot for privacy deletion.

        Any malformed or ownerless legacy value makes the proof incomplete.
        Such data is never loaded and the delete remains retryable until its
        short TTL expires.
        """
        session_targets: list[str] = []
        focus_targets: list[str] = []
        max_ttl = _OWNER_FENCE_TTL
        for prefix, owner_field, targets in (
            (_KEY_PREFIX, "owner_user_id", session_targets),
            (_FOCUS_PREFIX, "_owner_user_id", focus_targets),
        ):
            cursor: int | str = 0
            while True:
                cursor, keys = await r.scan(
                    cursor=cursor, match=prefix + "*", count=128)
                for key in keys:
                    raw = await r.get(key)
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                        # 挂起表是**一个 list**（Q1-C）；focus 仍是单对象，旧部署
                        # 留下的挂起也可能是单对象。三种形状都要能归属到 owner，
                        # 否则隐私删除会因为「证据不完整」永远失败。
                        items = (payload if isinstance(payload, list)
                                 else [payload])
                        owners = {str(it.get(owner_field) or "").strip()
                                  for it in items}
                    except (AttributeError, TypeError, ValueError,
                            json.JSONDecodeError):
                        return False, [], [], max_ttl
                    if not items or len(owners) != 1:
                        return False, [], [], max_ttl   # 混装 owner = 归属不清
                    owner = owners.pop()
                    if not owner:
                        return False, [], [], max_ttl
                    if owner == user_id:
                        targets.append(str(key))
                        ttl = int(await r.ttl(key))
                        if ttl > max_ttl:
                            max_ttl = ttl
                if int(cursor) == 0:
                    break
        return True, session_targets, focus_targets, max_ttl

    async def delete_owner(self, user_id: str) -> bool:
        """Delete all pending/focus state and retain a write tombstone."""
        user_id = str(user_id or "").strip()
        if not user_id:
            return False

        # Always purge process-local fallback first.  Even when Redis is the
        # authoritative backend and unavailable, these copies must not revive.
        # The fence covers the longest remaining record lifetime, otherwise a
        # request that started before deletion could re-save a 600s record once
        # a fixed 300s tombstone expires.
        memory_fence_expires = time.time() + _OWNER_FENCE_TTL
        for key, (entries, expires) in list(self._mem.items()):
            # 一个 key 下是一张挂起表（Q1-C）；同键必然同 owner（键含 owner 摘要），
            # 但仍逐条比对——归属判定不建立在「键长这样」这个约定上。
            if any(str(s.owner_user_id or "").strip() == user_id
                   for s in entries):
                memory_fence_expires = max(memory_fence_expires, expires)
                self._mem.pop(key, None)
        for key, (focus, expires) in list(self._focus_mem.items()):
            if str(focus.get("_owner_user_id") or "").strip() == user_id:
                memory_fence_expires = max(memory_fence_expires, expires)
                self._focus_mem.pop(key, None)
        self._owner_fences[user_id] = max(
            self._owner_fences.get(user_id, 0.0),
            memory_fence_expires,
        )

        r = await self._redis()
        if self._url and r is None:
            return False
        if not r:
            return True

        owner_key = self._owner_key(user_id)
        fence_key = self._owner_fence_key(user_id)
        try:
            fence = await r.get(fence_key)
            if fence == _DELETED:
                ok, sessions, focuses, _ = await self._scan_owner_records(
                    r, user_id)
                if not ok or sessions or focuses or await r.exists(owner_key):
                    return False
                tombstone_ttl = int(await r.ttl(fence_key))
                if tombstone_ttl < _OWNER_FENCE_TTL:
                    await r.expire(fence_key, _OWNER_FENCE_TTL)
                return True
            if fence:
                return False
            if not await r.set(
                    fence_key, _DELETING, nx=True, ex=_OWNER_FENCE_TTL):
                return False

            ok, sessions, focuses, tombstone_ttl = (
                await self._scan_owner_records(r, user_id))
            if not ok:
                return False
            pipe = r.pipeline(transaction=True)
            if sessions:
                pipe.delete(*sessions)
            if focuses:
                pipe.delete(*focuses)
            pipe.delete(owner_key)
            await pipe.execute()

            ok, sessions, focuses, _ = await self._scan_owner_records(
                r, user_id)
            if (not ok or sessions or focuses
                    or await r.exists(owner_key)):
                return False
            await r.set(
                fence_key, _DELETED,
                ex=max(_OWNER_FENCE_TTL, int(tombstone_ttl)),
            )
            return True
        except Exception as exc:
            logger.warning(
                "SessionStore owner delete failed: %s", type(exc).__name__)
            return False

    # Focus state is owner-bound and independently TTL'd.

    async def load_focus(self, session_id: str, *,
                         owner_user_id: str = "") -> dict | None:
        """加载会话焦点（dict）。TTL 过期返回 None。"""
        owner = str(owner_user_id or "").strip()
        if not owner or not str(session_id or "").strip():
            return None
        r = await self._redis()
        if self._url and r is None:
            return None
        key = self._focus_key(owner, session_id)
        if r:
            raw = await r.eval(
                _LOAD_OWNER_LUA, 2, key, self._owner_fence_key(owner))
            try:
                value = json.loads(raw) if raw else None
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            if not isinstance(value, dict):
                return None
            if str(value.get("_owner_user_id") or "").strip() != owner:
                return None
            return {k: v for k, v in value.items()
                    if k != "_owner_user_id"}
        if self._memory_tombstoned(owner):
            return None
        entry = self._focus_mem.get(key)
        if entry:
            data, expire_ts = entry
            if (time.time() < expire_ts
                    and str(data.get("_owner_user_id") or "").strip() == owner):
                return {k: v for k, v in data.items()
                        if k != "_owner_user_id"}
            del self._focus_mem[key]
        return None

    async def save_focus(self, session_id: str, focus: dict, *,
                         owner_user_id: str = "") -> bool:
        """保存会话焦点（dict）。每轮成功后更新；独立 _FOCUS_TTL。"""
        owner = str(owner_user_id or "").strip()
        if not owner or not str(session_id or "").strip():
            return False
        r = await self._redis()
        if self._url and r is None:
            return False
        key = self._focus_key(owner, session_id)
        # Store owner beside the focus payload so user-all deletion can remove
        # this second planner session family as well.
        value = {**dict(focus or {}), "_owner_user_id": owner}
        data = json.dumps(value, ensure_ascii=False, default=str)
        if r:
            saved = await r.eval(
                _SAVE_FOCUS_LUA, 2, key,
                self._owner_fence_key(owner), data, _FOCUS_TTL)
            if int(saved or 0) != 1:
                return False
        else:
            if self._memory_tombstoned(owner):
                return False
            self._focus_mem[key] = (value, time.time() + _FOCUS_TTL)
        return True
