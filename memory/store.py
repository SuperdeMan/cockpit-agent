"""记忆存储：短期会话(Redis，内存兜底) + 上下文 scope 取数 + 画像管理。

Phase 1 改进：画像导出/删除（合规）、scope 权限控制。
"""
from __future__ import annotations
import json
import os
import time
import logging

logger = logging.getLogger("memory.store")

try:
    import redis.asyncio as aioredis
except Exception:
    aioredis = None

_MOCK_CONTEXT = {
    "vehicle.location": {"lat": 31.23, "lng": 121.47, "city": "上海", "road": "延安高架"},
    "vehicle.state": {"speed_kmh": 60, "soc": 55, "gear": "D"},
    "profile.taste": {"spicy": "medium", "budget_per_person": 100},
}

# 敏感 scope（上云需脱敏）
_SENSITIVE_SCOPES = {"vehicle.location", "vehicle.state", "profile.taste"}


class MemoryStore:
    def __init__(self):
        self.url = os.getenv("REDIS_URL", "")
        self._r = None
        self._mem: dict[str, list] = {}
        self._profiles: dict[str, dict] = {}  # user_id -> profile data

    async def _redis(self):
        if aioredis and self.url and self._r is None:
            try:
                self._r = aioredis.from_url(self.url, decode_responses=True)
                await self._r.ping()
            except Exception as e:
                logger.warning("Redis unavailable, using in-memory: %s", e)
                self._r = None
        return self._r

    async def append_turn(self, session_id: str, role: str, text: str):
        turn = {"role": role, "text": text, "ts": int(time.time())}
        r = await self._redis()
        key = f"sess:{session_id}"
        if r:
            await r.rpush(key, json.dumps(turn))
            await r.ltrim(key, -50, -1)
        else:
            self._mem.setdefault(session_id, []).append(turn)

    async def get_session(self, session_id: str, last_n: int) -> list[dict]:
        r = await self._redis()
        if r:
            items = await r.lrange(f"sess:{session_id}", -last_n, -1)
            return [json.loads(i) for i in items]
        return self._mem.get(session_id, [])[-last_n:]

    async def get_context(self, session_id, user_id, vehicle_id, scopes) -> dict:
        """按 scope 取上下文。敏感 scope 走脱敏路径。"""
        result = {}
        for scope in scopes:
            # 用户画像优先从 _profiles 取
            if scope.startswith("profile.") and user_id:
                profile = self._profiles.get(user_id, {})
                key = scope.split(".", 1)[1] if "." in scope else scope
                if key in profile:
                    result[scope] = json.dumps(profile[key], ensure_ascii=False)
                    continue
            # 兜底 mock
            if scope in _MOCK_CONTEXT:
                data = _MOCK_CONTEXT[scope]
                # 敏感 scope 脱敏（如位置只给城市级）
                if scope in _SENSITIVE_SCOPES:
                    data = self._desensitize(scope, data)
                result[scope] = json.dumps(data, ensure_ascii=False)
        return result

    async def export_profile(self, user_id: str) -> dict:
        """导出用户画像（合规接口）。"""
        return self._profiles.get(user_id, {})

    async def delete_profile(self, user_id: str) -> bool:
        """删除用户画像（合规接口）。删除后不可再被检索。"""
        if user_id in self._profiles:
            del self._profiles[user_id]
            logger.info("Profile deleted: %s", user_id)
            return True
        return False

    async def update_profile(self, user_id: str, data: dict):
        """更新用户画像。"""
        if user_id not in self._profiles:
            self._profiles[user_id] = {}
        self._profiles[user_id].update(data)

    @staticmethod
    def _desensitize(scope: str, data: dict) -> dict:
        """敏感数据脱敏。"""
        if scope == "vehicle.location":
            return {"city": data.get("city", ""), "road": ""}  # 只给城市，不给精确位置
        return data
