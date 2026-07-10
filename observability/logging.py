"""结构化日志 + 敏感字段脱敏。"""
from __future__ import annotations
import logging
import json
import os
import sys

from .redact import SENSITIVE_PATTERNS as _SHARED_PATTERNS


class StructuredFormatter(logging.Formatter):
    """结构化 JSON 日志格式。敏感字段自动脱敏（规则与观测事件共享 redact.py）。"""

    SENSITIVE_PATTERNS = _SHARED_PATTERNS

    def format(self, record):
        log_data = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # 附加 trace_id / session_id（如有）——badcase 排查按 id 直接 grep/检索
        from .tracing import get_session_id, get_trace_id
        tid = get_trace_id()
        if tid:
            log_data["trace_id"] = tid
        sid = get_session_id()
        if sid:
            log_data["session_id"] = sid

        # 附加额外字段
        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        text = json.dumps(log_data, ensure_ascii=False)
        return self._desensitize(text)

    def _desensitize(self, text: str) -> str:
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


class NatsLogHandler(logging.Handler):
    """把结构化日志复制一份经 EventEmitter 发 obs.log（best-effort，collector 落库）。

    发射规则：级别 ≥ LOG_SHIP_LEVEL（默认 WARNING）恒发；带 trace_id 的 INFO 也发
    ——保证 badcase 轮次详情页有可读的执行痕迹，而全局 INFO 噪音不进 collector。

    自激励防护（关键）：EventEmitter/nats 客户端自身的日志（obs.* / nats*）绝不转发，
    否则「发送失败的日志→再触发发送」会形成风暴循环。emit 内任何异常吞掉。
    """

    _EXCLUDE_PREFIXES = ("obs.", "nats", "asyncio")

    def __init__(self, service: str, ship_level: int | None = None):
        super().__init__(logging.NOTSET)
        self.service = service
        if ship_level is None:
            name = os.getenv("LOG_SHIP_LEVEL", "WARNING").upper()
            ship_level = getattr(logging, name, logging.WARNING)
        self.ship_level = ship_level

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(self._EXCLUDE_PREFIXES):
                return
            from .tracing import get_session_id, get_trace_id
            trace_id = get_trace_id()
            if record.levelno < self.ship_level and not trace_id:
                return

            from .events import get_emitter
            from .redact import redact
            emitter = get_emitter(self.service)
            if emitter._disabled:
                return
            payload = {
                "level": record.levelname,
                "logger": record.name,
                "msg": redact(record.getMessage())[:1000],
                "trace_id": trace_id,
                "session_id": get_session_id(),
            }
            # 有事件循环（async 服务的常态）才入队；无循环（启动早期/线程）直接放弃，
            # 不能为日志阻塞或起新循环。
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(emitter._emit("obs.log", payload))
        except Exception:
            pass


def setup_structured_logging(level: str = "INFO", service: str = ""):
    """配置结构化日志（stdout JSON + 可选 obs.log 上报）。

    service 非空时挂 NatsLogHandler：日志按 trace 进 collector，dashboard
    轮次详情/日志页可检索——badcase 排查不再逐容器 docker logs。
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
    handlers: list[logging.Handler] = [handler]
    if service:
        handlers.append(NatsLogHandler(service))
    root.handlers = handlers

    # 降低 gRPC/urllib3 等库的日志级别
    for name in ("grpc", "urllib3", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("otel.tracing").info("Structured logging initialized at %s level", level)
