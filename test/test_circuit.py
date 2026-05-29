"""熔断器测试。"""
import time
from orchestrator.cloud.circuit import CircuitBreaker, CircuitBreakerManager, CLOSED, OPEN, HALF_OPEN


def test_initial_closed():
    cb = CircuitBreaker(failure_threshold=3)
    assert cb.state == CLOSED
    assert cb.allow() is True


def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == OPEN
    assert cb.allow() is False


def test_half_open_after_timeout():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == OPEN
    time.sleep(0.02)
    assert cb.allow() is True  # enters HALF_OPEN
    assert cb.state == HALF_OPEN


def test_closes_on_success_in_half_open():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb.allow()  # HALF_OPEN
    cb.record_success()
    assert cb.state == CLOSED


def test_reopens_on_failure_in_half_open():
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.02)
    cb.allow()  # HALF_OPEN
    cb.record_failure()
    assert cb.state == OPEN


def test_manager():
    mgr = CircuitBreakerManager(failure_threshold=2)
    cb1 = mgr.get("endpoint-a")
    cb2 = mgr.get("endpoint-b")
    cb1.record_failure()
    cb1.record_failure()
    assert cb1.state == OPEN
    assert cb2.state == CLOSED  # 不互相影响
    snap = mgr.snapshot()
    assert snap["endpoint-a"]["state"] == OPEN
