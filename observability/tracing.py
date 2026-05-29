"""OpenTelemetry trace 贯通。trace_id 从 HMI → Edge → Cloud → Agent 全链路。

WS9 核心：每个请求带 trace_id，所有服务透传。
有 OTEL_EXPORTER_OTLP_ENDPOINT 时接真实 OTel SDK；否则用简化版。
"""
from __future__ import annotations
import os
import uuid
import logging

logger = logging.getLogger("otel.tracing")

# 全局 trace 上下文（简化版，OTel SDK 未就绪时使用）
_current_trace: dict[str, str] = {}

# OTel SDK 句柄（延迟初始化）
_tracer = None


def setup_tracing(service_name: str = "cockpit"):
    """初始化 tracing。有 endpoint 时接 OTel SDK；否则简化版。"""
    global _tracer
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    if endpoint:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource

            resource = Resource.create({"service.name": service_name})
            provider = TracerProvider(resource=resource)
            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            _tracer = trace.get_tracer(service_name)
            logger.info("OTel tracing initialized: %s (service=%s)", endpoint, service_name)
            return True
        except ImportError:
            logger.warning("OTel SDK not installed, falling back to simplified tracing. "
                           "Install: pip install opentelemetry-api opentelemetry-sdk "
                           "opentelemetry-exporter-otlp-proto-grpc")
        except Exception as e:
            logger.warning("OTel setup failed: %s, falling back to simplified tracing", e)

    logger.info("Using simplified tracing (no OTLP endpoint)")
    return True


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def get_trace_id() -> str:
    if _tracer:
        from opentelemetry import trace
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            return format(ctx.trace_id, '032x')
    return _current_trace.get("trace_id", "")


def set_trace_id(trace_id: str):
    _current_trace["trace_id"] = trace_id


def trace_context_from_meta(meta: dict) -> str:
    tid = meta.get("trace_id", "")
    if not tid:
        tid = new_trace_id()
    set_trace_id(tid)
    return tid


def inject_trace_meta(meta: dict) -> dict:
    tid = get_trace_id()
    if tid:
        meta["trace_id"] = tid
    return meta


def start_span(name: str):
    """创建 span（OTel SDK 可用时用真实 span，否则用 no-op）。"""
    if _tracer:
        return _tracer.start_as_current_span(name)
    # No-op context manager
    from contextlib import contextmanager
    @contextmanager
    def _noop():
        yield
    return _noop()
