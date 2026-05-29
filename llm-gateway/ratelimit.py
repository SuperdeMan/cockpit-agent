"""令牌桶限流。防止单用户/全局过载。"""
from __future__ import annotations
import time
import logging

logger = logging.getLogger("llm.ratelimit")


class TokenBucket:
    """令牌桶限流器。"""

    def __init__(self, rate: float = 10, capacity: float = 20):
        """
        rate: 每秒补充的令牌数
        capacity: 桶容量（允许的突发量）
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()

    def allow(self, cost: int = 1) -> bool:
        """消耗 cost 个令牌。返回是否允许。"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= cost:
            self.tokens -= cost
            return True
        logger.warning("Rate limited: tokens=%.1f, cost=%d", self.tokens, cost)
        return False


class RateLimiter:
    """全局 + 每 key 限流。"""

    def __init__(self, global_rate: float = 20, global_capacity: float = 50,
                 per_key_rate: float = 5, per_key_capacity: float = 10):
        self.global_bucket = TokenBucket(global_rate, global_capacity)
        self.per_key_rate = per_key_rate
        self.per_key_capacity = per_key_capacity
        self._buckets: dict[str, TokenBucket] = {}

    def _get_bucket(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(self.per_key_rate, self.per_key_capacity)
        return self._buckets[key]

    def allow(self, key: str = "default", cost: int = 1) -> bool:
        if not self.global_bucket.allow(cost):
            return False
        return self._get_bucket(key).allow(cost)
