"""可观测模块：trace 贯通 + 结构化日志 + 核心指标。"""
from .tracing import setup_tracing, get_trace_id
from .logging import setup_structured_logging
from .metrics import MetricsCollector
