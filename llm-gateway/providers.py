"""LLM Provider 抽象与实现。新增厂商在此扩展，对上层透明。

支持的 provider：
- anthropic: Anthropic Claude API
- xiaomimimo: 小米 MiMo API（OpenAI 兼容格式）
- mock: 无 key 时的回显兜底
"""
from __future__ import annotations
import json
import os


class BaseProvider:
    async def complete(self, messages, model, temperature, max_tokens):
        """returns (content, model_used, finish_reason, (prompt_tokens, completion_tokens))"""
        raise NotImplementedError

    async def stream(self, messages, model, temperature, max_tokens):
        raise NotImplementedError
        yield  # pragma: no cover


class MockProvider(BaseProvider):
    """无 API key 时的兜底，保证 PoC 可离线端到端跑通。"""
    async def complete(self, messages, model, temperature, max_tokens):
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = f"[mock] 我听到你说「{user}」。配置 LLM_API_KEY 后即可接入真实模型。"
        return text, "mock", "stop", (0, 0)

    async def stream(self, messages, model, temperature, max_tokens):
        content, *_ = await self.complete(messages, model, temperature, max_tokens)
        for ch in content:
            yield ch


class AnthropicProvider(BaseProvider):
    def __init__(self, api_key: str):
        from anthropic import AsyncAnthropic
        self.client = AsyncAnthropic(api_key=api_key)

    @staticmethod
    def _split(messages):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [{"role": m["role"], "content": m["content"]}
                for m in messages if m["role"] in ("user", "assistant")]
        return system or None, msgs

    async def complete(self, messages, model, temperature, max_tokens):
        system, msgs = self._split(messages)
        resp = await self.client.messages.create(
            model=model, system=system, messages=msgs,
            temperature=temperature, max_tokens=max_tokens or 512)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, model, resp.stop_reason, (resp.usage.input_tokens, resp.usage.output_tokens)

    async def stream(self, messages, model, temperature, max_tokens):
        system, msgs = self._split(messages)
        async with self.client.messages.stream(
                model=model, system=system, messages=msgs,
                temperature=temperature, max_tokens=max_tokens or 512) as s:
            async for text in s.text_stream:
                yield text


class MiMoProvider(BaseProvider):
    """小米 MiMo API（OpenAI 兼容格式）。

    endpoint: https://api.xiaomimimo.com/v1/chat/completions
    auth: api-key header
    docs: https://platform.xiaomimimo.com/docs/zh-CN/quick-start/first-api-call
    """
    BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def complete(self, messages, model, temperature, max_tokens):
        import httpx
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens or 512,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        return content, model, "stop", (prompt_tokens, completion_tokens)

    async def stream(self, messages, model, temperature, max_tokens):
        import httpx
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens or 512,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", self.BASE_URL, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = chunk["choices"][0].get("delta", {})
                        text = delta.get("content", "")
                        if text:
                            yield text
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue


def build_provider() -> BaseProvider:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    api_key = os.getenv("LLM_API_KEY", "")

    if provider in ("anthropic",) and api_key:
        return AnthropicProvider(api_key)
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoProvider(api_key)
    if api_key and provider == "openai":
        # OpenAI 兼容（未来扩展）
        return MiMoProvider(api_key)  # MiMo 兼容 OpenAI 格式，可复用

    print(f"[llm-gateway] provider={provider}, no API key -> MockProvider", flush=True)
    return MockProvider()
