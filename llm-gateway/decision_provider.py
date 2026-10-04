"""Jev（typesafe.ai）判别的唯一外呼。

`POST {base}/v1/systemone`，`Authorization: Bearer <key>`，请求体 `{model, state, questions}`。
在线链不重试（429 / 529 / 超时直接回基线，由调用方的总预算兜底）；鉴权失败是配置错误，不循环请求。
连接复用一个长连接客户端：2026-10-04 实测建连（跨境 TLS）首次约 2 s、经本机代理约 17 s，复用后往返约 0.2 s——
每次新建连接会把每一次判别都拖成秒级。
**请求头与正文一律不进日志**（凭证、原话都在里面）。
"""
from __future__ import annotations

import httpx

from runtime import decision_contract as dc

DEFAULT_BASE_URL = "https://api.typesafe.ai"
ENDPOINT = "/v1/systemone"


class DecisionProviderError(RuntimeError):
    """外呼失败：status 是 `runtime.decision_contract` 的状态短码，reason 是受控原因码。"""

    def __init__(self, status: str, reason: str):
        super().__init__(f"{status}:{reason}")
        self.status = status
        self.reason = reason


class TypeSafeDecisionProvider:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, *, transport=None):
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def decide(self, *, model: str, state, questions: dict, timeout_s: float) -> dict:
        if not self._api_key:
            raise DecisionProviderError(dc.UNAVAILABLE, "no_credential")
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body = {"model": model, "state": state, "questions": questions}
        try:
            resp = await self._http().post(ENDPOINT, json=body, headers=headers, timeout=httpx.Timeout(timeout_s))
        except httpx.TimeoutException:
            raise DecisionProviderError(dc.TIMEOUT, "timeout") from None
        except httpx.HTTPError:
            raise DecisionProviderError(dc.UNAVAILABLE, "network") from None
        code = resp.status_code
        if code in (401, 403):
            raise DecisionProviderError(dc.UNAVAILABLE, "auth")
        if code == 422:
            raise DecisionProviderError(dc.INVALID_REQUEST, "vendor_rejected")
        if code == 429:
            raise DecisionProviderError(dc.UNAVAILABLE, "rate_limited")
        if code == 529:
            raise DecisionProviderError(dc.UNAVAILABLE, "overloaded")
        if code != 200:
            raise DecisionProviderError(dc.UNAVAILABLE, f"http_{code}")
        try:
            data = resp.json()
        except ValueError:
            raise DecisionProviderError(dc.INVALID_RESPONSE, "bad_json") from None
        if not isinstance(data, dict):
            raise DecisionProviderError(dc.INVALID_RESPONSE, "bad_json")
        return data


class FakeDecisionProvider:
    """确定性假 provider：按问题给出合法答案（测试、off 等价对照用）；可注入固定答案或异常。"""

    def __init__(self, model: str = "jev-1.13.0", *, answers=None, error: Exception | None = None,
                 usage: dict | None = None):
        self.model = model
        self.answers = answers
        self.error = error
        self.usage = usage if usage is not None else {"input_tokens": 100, "output_tokens": 0}
        self.calls: list[dict] = []

    @property
    def configured(self) -> bool:
        return True

    async def decide(self, *, model: str, state, questions: dict, timeout_s: float) -> dict:
        self.calls.append({"model": model, "state": state, "questions": questions, "timeout_s": timeout_s})
        if self.error is not None:
            raise self.error
        answers = self.answers if self.answers is not None else {
            qid: self._answer(q) for qid, q in questions.items()}
        out = {"model": self.model, "answers": answers}
        if self.usage:
            out["usage"] = dict(self.usage)
        return out

    @staticmethod
    def _answer(question: dict) -> dict:
        kind = question["type"]
        if kind == "noul":
            return {"type": "noul", "noul": 0.5}
        if kind == "choice":
            options = list(question["criteria"])
            share = 1.0 / len(options)
            return {"type": "choice", "choice": options[0], "probabilities": {o: share for o in options},
                    "confidence": share}
        levels = len(question["criteria"])
        share = 1.0 / levels
        return {"type": "score", "score": (levels - 1) / 2, "probabilities": {str(i): share for i in range(levels)},
                "confidence": share}
