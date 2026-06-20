"""SDK 出站 HTTP 客户端：给 Agent 的 provider 调真实外部 API（高德 / 和风 等）用。

统一封装超时 / 有界重试 / 每-provider 熔断 / 结构化错误，并 best-effort 发 provider
调用子 span（复用 observability/events.py，进现有 collector→Dashboard 的 trace 视图）。

provider 实现只管「发请求、解析响应」；失败抛 ProviderError 子类，Agent 侧据此降级
（回退 mock / 部分结果），不让外部抖动击穿主链。无 observability 包或无 NATS 时静默不发 span。
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("sdk.http")

# observability 为可选依赖：镜像未带 observability/ 或无 NATS 时优雅降级（不发 span、不影响主链）。
try:
    from observability.events import get_emitter
except Exception:  # pragma: no cover - 缺包时降级
    get_emitter = None


class ProviderError(RuntimeError):
    """provider 调用失败基类。Agent 捕获后降级（回退 mock / 部分结果）。"""


class ProviderTimeout(ProviderError):
    """请求超时。"""


class ProviderHTTPError(ProviderError):
    """收到非 2xx 响应。"""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class ProviderUnavailable(ProviderError):
    """连接失败 / 熔断打开 / 重试耗尽。"""


class _CircuitBreaker:
    """每-provider 轻量熔断：连续失败 fail_threshold 次→打开冷却 cooldown_s 秒，期间短路；
    冷却后半开放一个探测，成功则复位。进程内、单实例，够 PoC 防止持续打死外部。"""

    def __init__(self, fail_threshold: int = 5, cooldown_s: float = 30.0):
        self.fail_threshold = fail_threshold
        self.cooldown_s = cooldown_s
        self._fails = 0
        self._opened_at = 0.0

    def allow(self) -> bool:
        if self._fails < self.fail_threshold:
            return True
        return time.monotonic() - self._opened_at >= self.cooldown_s  # 半开探测

    def record_success(self) -> None:
        self._fails = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._fails += 1
        if self._fails >= self.fail_threshold:
            self._opened_at = time.monotonic()


class AsyncHttpClient:
    """provider 共享的 async HTTP 客户端。每个 provider 实例持有一个，复用连接池。

    用法::

        self._http = AsyncHttpClient(vendor="amap", service="navigation")
        data = await self._http.get_json(url, params={...}, op="place_text", meta=meta)
    """

    def __init__(self, vendor: str, service: str = "agent",
                 timeout_s: float = 3.0, max_retries: int = 1,
                 fail_threshold: int = 5, cooldown_s: float = 30.0):
        self.vendor = vendor
        self.service = service
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 2.0)))
        self._breaker = _CircuitBreaker(fail_threshold, cooldown_s)
        self._emitter = get_emitter(service) if get_emitter else None

    async def get_json(self, url: str, params: dict | None = None,
                       op: str = "get", headers: dict | None = None,
                       meta: dict | None = None) -> Any:
        """GET 并解析 JSON。带超时 / 重试（仅瞬时错误）/ 熔断；失败抛 ProviderError 子类。

        4xx 视为确定性错误（key/参数错），不重试、立即失败；超时 / 连接错 / 5xx 视为瞬时，
        在重试预算内退避重试。每次调用 best-effort 发一条 ``provider.<vendor>.<op>`` span。
        """
        if not self._breaker.allow():
            await self._emit_span(op, "error", 0.0, {"outcome": "circuit_open"}, meta)
            raise ProviderUnavailable(f"{self.vendor} circuit open")

        start = time.monotonic()
        last_exc: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.get(url, params=params, headers=headers)
            except httpx.TimeoutException as e:
                last_exc = ProviderTimeout(f"{self.vendor} timeout: {e}")
            except httpx.HTTPError as e:
                last_exc = ProviderUnavailable(f"{self.vendor} unavailable: {e}")
            else:
                if resp.status_code >= 500:
                    last_exc = ProviderHTTPError(resp.status_code, resp.text[:200])
                    # 5xx 视为瞬时，落到下方重试
                elif resp.status_code >= 400:
                    self._breaker.record_failure()
                    dur = (time.monotonic() - start) * 1000
                    await self._emit_span(op, "error", dur,
                                          {"http_status": resp.status_code,
                                           "outcome": "http_error"}, meta)
                    raise ProviderHTTPError(resp.status_code, resp.text[:200])
                else:
                    data = resp.json()
                    self._breaker.record_success()
                    dur = (time.monotonic() - start) * 1000
                    await self._emit_span(op, "ok", dur,
                                          {"http_status": resp.status_code,
                                           "outcome": "ok"}, meta)
                    return data
            if attempt < self.max_retries:
                await asyncio.sleep(0.15 * (2 ** attempt))

        self._breaker.record_failure()
        dur = (time.monotonic() - start) * 1000
        outcome = "timeout" if isinstance(last_exc, ProviderTimeout) else "unavailable"
        await self._emit_span(op, "error", dur, {"outcome": outcome}, meta)
        raise last_exc or ProviderUnavailable(f"{self.vendor} failed")

    async def _emit_span(self, op: str, status: str, duration_ms: float,
                         attrs: dict, meta: dict | None) -> None:
        if not self._emitter:
            return
        meta = meta or {}
        attrs = dict(attrs or {})
        attrs.setdefault("vendor", self.vendor)
        try:
            await self._emitter.emit_span(
                trace_id=meta.get("trace_id", ""),
                node=f"provider.{self.vendor}.{op}",
                status=status,
                duration_ms=duration_ms,
                attrs=attrs,
                parent_id=meta.get("span_id", ""),
            )
        except Exception as e:  # pragma: no cover - 可观测绝不影响主链
            logger.debug("emit provider span failed: %s", e)

    async def post_json(self, url: str, json_body: dict | None = None,
                        op: str = "post", headers: dict | None = None,
                        meta: dict | None = None) -> Any:
        """POST JSON 并解析响应。带超时 / 重试 / 熔断，逻辑与 get_json 一致。"""
        if not self._breaker.allow():
            await self._emit_span(op, "error", 0.0, {"outcome": "circuit_open"}, meta)
            raise ProviderUnavailable(f"{self.vendor} circuit open")

        start = time.monotonic()
        last_exc: ProviderError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self._client.post(url, json=json_body, headers=headers)
            except httpx.TimeoutException as e:
                last_exc = ProviderTimeout(f"{self.vendor} timeout: {e}")
            except httpx.HTTPError as e:
                last_exc = ProviderUnavailable(f"{self.vendor} unavailable: {e}")
            else:
                if resp.status_code >= 500:
                    last_exc = ProviderHTTPError(resp.status_code, resp.text[:200])
                elif resp.status_code >= 400:
                    self._breaker.record_failure()
                    dur = (time.monotonic() - start) * 1000
                    await self._emit_span(op, "error", dur,
                                          {"http_status": resp.status_code,
                                           "outcome": "http_error"}, meta)
                    raise ProviderHTTPError(resp.status_code, resp.text[:200])
                else:
                    data = resp.json()
                    self._breaker.record_success()
                    dur = (time.monotonic() - start) * 1000
                    await self._emit_span(op, "ok", dur,
                                          {"http_status": resp.status_code,
                                           "outcome": "ok"}, meta)
                    return data
            if attempt < self.max_retries:
                await asyncio.sleep(0.15 * (2 ** attempt))

        self._breaker.record_failure()
        dur = (time.monotonic() - start) * 1000
        outcome = "timeout" if isinstance(last_exc, ProviderTimeout) else "unavailable"
        await self._emit_span(op, "error", dur, {"outcome": outcome}, meta)
        raise last_exc or ProviderUnavailable(f"{self.vendor} failed")

    async def aclose(self) -> None:
        await self._client.aclose()
