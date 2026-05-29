"""结构化日志 + 敏感字段脱敏。"""
from __future__ import annotations
import logging
import json
import re
import sys


class StructuredFormatter(logging.Formatter):
    """结构化 JSON 日志格式。敏感字段自动脱敏。"""

    SENSITIVE_PATTERNS = [
        (re.compile(r'password["\s:=]+\S+', re.IGNORECASE), 'password=***'),
        (re.compile(r'token["\s:=]+\S+', re.IGNORECASE), 'token=***'),
        (re.compile(r'api[_-]?key["\s:=]+\S+', re.IGNORECASE), 'api_key=***'),
        (re.compile(r'\b\d{11,}\b'), '***'),  # 手机号等长数字
    ]

    def format(self, record):
        log_data = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # 附加 trace_id（如有）
        from .tracing import get_trace_id
        tid = get_trace_id()
        if tid:
            log_data["trace_id"] = tid

        # 附加额外字段
        if hasattr(record, "extra_data"):
            log_data.update(record.extra_data)

        text = json.dumps(log_data, ensure_ascii=False)
        return self._desensitize(text)

    def _desensitize(self, text: str) -> str:
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def setup_structured_logging(level: str = "INFO"):
    """配置结构化日志。"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
    root.handlers = [handler]

    # 降低 gRPC/urllib3 等库的日志级别
    for name in ("grpc", "urllib3", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.getLogger("otel.tracing").info("Structured logging initialized at %s level", level)
