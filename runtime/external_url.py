"""外部 URL 的一份判据（CA2-17 S3）：商户服务器地址、支付链接都按它判。

- `normalize_hostname`：白名单与 URL 里的主机名归一化（ASCII、非 IP、合法标签），不合法返回空串。
- `https_url_host`：干净的 https URL（443、无 userinfo、无空白与控制字符）才返回归一化主机，否则空串。
- `pay_url_allowed`：支付链接 = 干净的 https URL 且主机精确在白名单里。

商户桥（服务器 URL、两家工作流与通用写路径的支付链接）与支付网关共用这一份；此前三处各写一份、宽严不一。
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse


def normalize_hostname(value: str) -> str:
    """Return a canonical pure hostname, or empty for unsafe host syntax."""
    raw = str(value or "").strip().rstrip(".")
    # The allowlist is an audited configuration boundary, not a user-facing URL
    # parser.  Keep it ASCII-only so Python's legacy IDNA 2003 codec cannot turn
    # a Unicode lookalike such as ``faß.de`` into the distinct host ``fass.de``.
    if (not raw or not raw.isascii() or any(ch.isspace() for ch in raw) or
            any(ch in raw for ch in "/:@?#*[]")):
        return ""
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        return ""
    try:
        host = raw.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return ""
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2:
        return ""
    if any(not label or len(label) > 63 or label.startswith("-") or
           label.endswith("-") or not re.fullmatch(r"[a-z0-9-]+", label)
           for label in labels):
        return ""
    return host


def https_url_host(url) -> str:
    """干净的 https URL（443、无 userinfo、无空白与控制字符）返回归一化主机，否则空串。"""
    if not isinstance(url, str) or not url or url != url.strip():
        return ""
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return ""
    if (parsed.scheme.lower() != "https" or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)):
        return ""
    return normalize_hostname(parsed.hostname or "")


def pay_url_allowed(url, allowed_hosts) -> bool:
    """支付链接：干净的 https URL，且主机精确在白名单里（白名单同样归一化，空项丢弃）。"""
    host = https_url_host(url)
    allowed = {normalize_hostname(value) for value in (allowed_hosts or [])}
    allowed.discard("")
    return bool(host) and host in allowed
