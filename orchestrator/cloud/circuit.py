"""熔断器：防止单个 Agent endpoint 拖垮整条编排链。

三态：CLOSED（正常）→ OPEN（熔断，快速失败）→ HALF_OPEN（试探恢复）。
"""
from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("planner.circuit")

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5       # 连续失败次数触发熔断
    recovery_timeout: float = 30.0   # 熔断恢复超时（秒）
    half_open_max: int = 1           # 半开状态允许的试探次数

    _state: str = field(default=CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _last_failure: float = field(default=0, init=False)
    _half_open_calls: int = field(default=0, init=False)

    def allow(self) -> bool:
        """是否允许本次调用。"""
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            if time.monotonic() - self._last_failure > self.recovery_timeout:
                self._state = HALF_OPEN
                self._half_open_calls = 0
                logger.info("Circuit half-open for recovery probe")
                return True
            return False
        if self._state == HALF_OPEN:
            return self._half_open_calls < self.half_open_max
        return True

    def record_success(self):
        """调用成功。"""
        if self._state == HALF_OPEN:
            self._state = CLOSED
            self._failures = 0
            logger.info("Circuit closed (recovered)")
        self._failures = 0

    def record_failure(self):
        """调用失败。"""
        self._failures += 1
        self._last_failure = time.monotonic()
        if self._state == HALF_OPEN:
            self._state = OPEN
            logger.warning("Circuit re-opened (probe failed)")
        elif self._failures >= self.failure_threshold:
            self._state = OPEN
            logger.warning("Circuit opened (failures=%d)", self._failures)

    @property
    def state(self) -> str:
        return self._state


class CircuitBreakerManager:
    """按 endpoint 管理多个熔断器。"""

    def __init__(self, **kwargs):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._kwargs = kwargs

    def get(self, endpoint: str) -> CircuitBreaker:
        if endpoint not in self._breakers:
            self._breakers[endpoint] = CircuitBreaker(**self._kwargs)
        return self._breakers[endpoint]

    def snapshot(self) -> dict:
        return {ep: {"state": b.state, "failures": b._failures}
                for ep, b in self._breakers.items()}
