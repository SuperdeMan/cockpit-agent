"""SessionStore：多轮会话状态（待确认/待补槽），Redis 持久。

WS3 §6。支持 confirm/slot 续接 + TTL 超时作废。
"""
from __future__ import annotations
import json
import time
import logging
import os
from dataclasses import asdict

from .models import SessionState, Plan, Step, StepResult, StepStatus

logger = logging.getLogger("planner.session")

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

_KEY_PREFIX = "planner:sess:"
_DEFAULT_TTL = 90  # 秒
# 焦点态：与挂起态分开存（每轮持久、完成不清，供跨轮指代消解）。TTL 比挂起态长。
_FOCUS_PREFIX = "planner:focus:"
_FOCUS_TTL = 300  # 秒


class SessionStore:
    def __init__(self, redis_url: str = ""):
        self._url = redis_url or os.getenv("REDIS_URL", "")
        self._r = None
        self._mem: dict[str, tuple[SessionState, float]] = {}  # session_id -> (state, expire_ts)
        self._focus_mem: dict[str, tuple[dict, float]] = {}    # session_id -> (focus_dict, expire_ts)

    async def _redis(self):
        if aioredis and self._url and self._r is None:
            try:
                self._r = aioredis.from_url(
                    self._url, decode_responses=True, socket_timeout=3,
                    socket_connect_timeout=3, socket_keepalive=True,
                    health_check_interval=30, retry_on_timeout=True)
                await self._r.ping()
            except Exception as e:
                logger.warning("Redis unavailable, using in-memory: %s", e)
                self._r = None
        return self._r

    async def load(self, session_id: str) -> SessionState | None:
        """加载挂起的会话状态。TTL 过期返回 None。"""
        r = await self._redis()
        if r:
            raw = await r.get(f"{_KEY_PREFIX}{session_id}")
            if raw:
                data = json.loads(raw)
                return SessionState(**data)
            return None

        # 内存兜底
        entry = self._mem.get(session_id)
        if entry:
            state, expire_ts = entry
            if time.time() < expire_ts:
                return state
            del self._mem[session_id]
        return None

    async def save(self, session_id: str, state: SessionState):
        """保存挂起的会话状态。"""
        r = await self._redis()
        key = f"{_KEY_PREFIX}{session_id}"
        ttl = state.ttl_seconds or _DEFAULT_TTL
        data = json.dumps(asdict(state), ensure_ascii=False, default=str)

        if r:
            await r.set(key, data, ex=ttl)
        else:
            self._mem[session_id] = (state, time.time() + ttl)

    async def clear(self, session_id: str):
        """清除会话状态（任务完成或取消）。注意：不清焦点态——焦点跨轮存活供指代消解。"""
        r = await self._redis()
        if r:
            await r.delete(f"{_KEY_PREFIX}{session_id}")
        else:
            self._mem.pop(session_id, None)

    # ── 焦点态（跨轮指代消解；与挂起态分离，完成不清、独立 TTL）──

    async def load_focus(self, session_id: str) -> dict | None:
        """加载会话焦点（dict）。TTL 过期返回 None。"""
        r = await self._redis()
        if r:
            raw = await r.get(f"{_FOCUS_PREFIX}{session_id}")
            return json.loads(raw) if raw else None
        entry = self._focus_mem.get(session_id)
        if entry:
            data, expire_ts = entry
            if time.time() < expire_ts:
                return data
            del self._focus_mem[session_id]
        return None

    async def save_focus(self, session_id: str, focus: dict):
        """保存会话焦点（dict）。每轮成功后更新；独立 _FOCUS_TTL。"""
        r = await self._redis()
        data = json.dumps(focus, ensure_ascii=False, default=str)
        if r:
            await r.set(f"{_FOCUS_PREFIX}{session_id}", data, ex=_FOCUS_TTL)
        else:
            self._focus_mem[session_id] = (focus, time.time() + _FOCUS_TTL)
