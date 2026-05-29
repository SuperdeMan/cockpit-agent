"""LLM Gateway 成本与用量统计。"""
from __future__ import annotations
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger("llm.metrics")


@dataclass
class ModelStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0
    total_ms: float = 0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CostTracker:
    """按模型统计调用量、token 消耗、时延、错误率。"""

    # 粗略成本估算（$/1M tokens，仅供参考）
    COST_ESTIMATE = {
        "mimo-v2.5-pro": {"input": 2.0, "output": 8.0},
        "claude-opus-4-8": {"input": 15.0, "output": 75.0},
        "claude-haiku-4-5-20251001": {"input": 0.8, "output": 4.0},
    }

    def __init__(self):
        self._stats: dict[str, ModelStats] = defaultdict(ModelStats)

    def record(self, model: str, prompt_tokens: int, completion_tokens: int,
               latency_ms: float, error: bool = False):
        s = self._stats[model]
        s.calls += 1
        s.prompt_tokens += prompt_tokens
        s.completion_tokens += completion_tokens
        s.total_ms += latency_ms
        if error:
            s.errors += 1

    def snapshot(self) -> dict:
        result = {}
        for model, s in self._stats.items():
            cost_rate = self.COST_ESTIMATE.get(model, {"input": 0, "output": 0})
            est_cost = (s.prompt_tokens * cost_rate["input"] +
                        s.completion_tokens * cost_rate["output"]) / 1_000_000
            result[model] = {
                "calls": s.calls,
                "prompt_tokens": s.prompt_tokens,
                "completion_tokens": s.completion_tokens,
                "total_tokens": s.total_tokens,
                "avg_latency_ms": round(s.avg_ms, 1),
                "errors": s.errors,
                "error_rate": round(s.errors / s.calls, 3) if s.calls else 0,
                "estimated_cost_usd": round(est_cost, 4),
            }
        return result

    def log_summary(self):
        snap = self.snapshot()
        for model, stats in snap.items():
            logger.info("LLM [%s] calls=%d tokens=%d avg=%.0fms cost=$%.4f",
                        model, stats["calls"], stats["total_tokens"],
                        stats["avg_latency_ms"], stats["estimated_cost_usd"])


# 全局实例
cost_tracker = CostTracker()
