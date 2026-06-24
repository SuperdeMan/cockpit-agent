"""LLM 响应缓存（按 messages 哈希）。减少重复调用、降成本。"""
from __future__ import annotations
import hashlib
import json
import time
import logging
from collections import OrderedDict

logger = logging.getLogger("llm.cache")


class LLMCache:
    """LRU 缓存。key = messages 哈希，value = (content, model_used, ts)。"""

    def __init__(self, max_size: int = 256, ttl_seconds: int = 300):
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _hash(self, messages: list[dict], model: str, temperature: float,
              thinking=None) -> str:
        key_data = json.dumps(
            {"m": messages, "model": model, "t": temperature, "think": thinking},
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def get(self, messages: list[dict], model: str, temperature: float,
            thinking=None) -> tuple | None:
        h = self._hash(messages, model, temperature, thinking)
        entry = self._cache.get(h)
        if entry is None:
            self._misses += 1
            return None
        content, model_used, ts = entry
        if time.time() - ts > self._ttl:
            del self._cache[h]
            self._misses += 1
            return None
        self._hits += 1
        self._cache.move_to_end(h)
        logger.debug("Cache hit for %s", h[:8])
        return content, model_used, "stop", (0, 0)

    def put(self, messages: list[dict], model: str, temperature: float,
            content: str, model_used: str, thinking=None):
        h = self._hash(messages, model, temperature, thinking)
        self._cache[h] = (content, model_used, time.time())
        self._cache.move_to_end(h)
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0,
        }
