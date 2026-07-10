"""共享脱敏与内容采集门控。

日志（logging.StructuredFormatter）与观测事件（events.emit_turn / obs.llm）共用同一套
敏感字段规则——密钥/token/长数字不落任何观测面。内容级采集（用户原话/plan/LLM 输出）
由 OBS_CONTENT_CAPTURE 门控：PoC 默认 on；量产必须 off（.env.example 有注明），off 时
只保留长度与哈希指纹，链路形状排查不受影响。
"""
from __future__ import annotations

import hashlib
import os
import re

SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'password["\s:=]+\S+', re.IGNORECASE), 'password=***'),
    (re.compile(r'token["\s:=]+\S+', re.IGNORECASE), 'token=***'),
    (re.compile(r'api[_-]?key["\s:=]+\S+', re.IGNORECASE), 'api_key=***'),
    (re.compile(r'\b\d{11,}\b'), '***'),  # 手机号等长数字
]


def redact(text: str) -> str:
    """对文本应用全部敏感字段脱敏规则。"""
    for pattern, replacement in SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def content_capture_enabled() -> bool:
    """observability 是否采集内容级数据（默认 on；实时读 env，测试可 monkeypatch）。"""
    return os.getenv("OBS_CONTENT_CAPTURE", "on").lower() != "off"


def gate_content(text: str, limit: int = 2000) -> str:
    """内容采集门控：开→脱敏+截断；关→只留长度与哈希指纹（形状可查、内容不落盘）。"""
    if not text:
        return ""
    if not content_capture_enabled():
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        return f"<len={len(text)} sha={digest}>"
    out = redact(text)
    if len(out) > limit:
        out = out[:limit] + "…"
    return out
