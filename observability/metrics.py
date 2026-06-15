"""核心指标收集。WS9 指标：意图准确率/路由命中率/Agent时延/成功率/降级率/LLM成本。"""
from __future__ import annotations
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger("otel.metrics")


@dataclass
class MetricPoint:
    count: int = 0
    total_ms: float = 0
    errors: int = 0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0

    @property
    def error_rate(self) -> float:
        return self.errors / self.count if self.count else 0


class MetricsCollector:
    """内存指标收集器。定期上报或暴露 Prometheus endpoint。"""

    def __init__(self):
        self._intent: dict[str, MetricPoint] = defaultdict(MetricPoint)
        self._agent: dict[str, MetricPoint] = defaultdict(MetricPoint)
        self._route: dict[str, int] = defaultdict(int)  # local/cloud/degrade
        self._degrade: int = 0
        self._llm_tokens: int = 0

    def record_intent(self, intent: str, latency_ms: float, success: bool):
        """记录意图处理指标。"""
        m = self._intent[intent]
        m.count += 1
        m.total_ms += latency_ms
        if not success:
            m.errors += 1

    def record_agent_call(self, agent_id: str, latency_ms: float, success: bool):
        """记录 Agent 调用指标。"""
        m = self._agent[agent_id]
        m.count += 1
        m.total_ms += latency_ms
        if not success:
            m.errors += 1

    def record_route(self, route: str):
        """记录路由决策：local / cloud / degrade。"""
        self._route[route] += 1

    def record_degrade(self):
        """记录降级触发。"""
        self._degrade += 1

    def record_llm_tokens(self, tokens: int):
        """记录 LLM token 消耗。"""
        self._llm_tokens += tokens

    def snapshot(self) -> dict:
        """获取指标快照。"""
        return {
            "intents": {k: {"count": v.count, "avg_ms": round(v.avg_ms, 1),
                            "error_rate": round(v.error_rate, 3)}
                        for k, v in self._intent.items()},
            "agents": {k: {"count": v.count, "avg_ms": round(v.avg_ms, 1),
                           "error_rate": round(v.error_rate, 3)}
                       for k, v in self._agent.items()},
            "routes": dict(self._route),
            "degrade_count": self._degrade,
            "llm_tokens_total": self._llm_tokens,
        }

    def agent_snapshot(self, agent_id: str) -> dict | None:
        """Return cumulative metrics for one agent, if it has been called."""
        metric = self._agent.get(agent_id)
        if metric is None:
            return None
        return {
            "count": metric.count,
            "avg_ms": round(metric.avg_ms, 1),
            "error_rate": round(metric.error_rate, 3),
        }

    def log_summary(self):
        """输出指标摘要。"""
        snap = self.snapshot()
        logger.info("Metrics summary: routes=%s degrade=%d llm_tokens=%d",
                     snap["routes"], snap["degrade_count"], snap["llm_tokens_total"])


# 全局实例
metrics = MetricsCollector()
