"""LLM Gateway 缓存 + 限流 + 成本统计测试。"""
import time
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "llm-gateway"))
from cache import LLMCache
from ratelimit import RateLimiter, TokenBucket
from metrics import CostTracker


def test_cache_hit():
    c = LLMCache()
    msgs = [{"role": "user", "content": "hello"}]
    c.put(msgs, "mimo", 0.7, "world", "mimo")
    result = c.get(msgs, "mimo", 0.7)
    assert result is not None
    assert result[0] == "world"


def test_cache_miss_different_key():
    c = LLMCache()
    c.put([{"role": "user", "content": "a"}], "mimo", 0.7, "reply", "mimo")
    result = c.get([{"role": "user", "content": "b"}], "mimo", 0.7)
    assert result is None


def test_cache_ttl():
    c = LLMCache(ttl_seconds=0)
    msgs = [{"role": "user", "content": "x"}]
    c.put(msgs, "m", 0.7, "y", "m")
    time.sleep(0.01)
    assert c.get(msgs, "m", 0.7) is None


def test_cache_lru_eviction():
    c = LLMCache(max_size=2)
    for i in range(3):
        c.put([{"role": "user", "content": str(i)}], "m", 0.7, f"r{i}", "m")
    assert c.stats["size"] == 2


def test_cache_stats():
    c = LLMCache()
    msgs = [{"role": "user", "content": "q"}]
    c.put(msgs, "m", 0.7, "a", "m")
    c.get(msgs, "m", 0.7)  # hit
    c.get([{"role": "user", "content": "z"}], "m", 0.7)  # miss
    stats = c.stats
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_rate_limiter_allows():
    rl = RateLimiter(global_rate=100, global_capacity=100,
                     per_key_rate=100, per_key_capacity=100)
    assert rl.allow("user1") is True


def test_rate_limiter_denies():
    rl = RateLimiter(global_rate=1, global_capacity=1,
                     per_key_rate=1, per_key_capacity=1)
    rl.allow("user1")
    assert rl.allow("user1") is False


def test_token_bucket():
    tb = TokenBucket(rate=100, capacity=2)
    assert tb.allow(1) is True
    assert tb.allow(1) is True
    assert tb.allow(1) is False


def test_cost_tracker():
    ct = CostTracker()
    ct.record("mimo-v2.5-pro", 100, 200, 500.0)
    ct.record("mimo-v2.5-pro", 50, 100, 300.0)
    snap = ct.snapshot()
    assert snap["mimo-v2.5-pro"]["calls"] == 2
    assert snap["mimo-v2.5-pro"]["prompt_tokens"] == 150


def test_cost_tracker_error():
    ct = CostTracker()
    ct.record("mimo", 0, 0, 100, error=True)
    snap = ct.snapshot()
    assert snap["mimo"]["errors"] == 1
