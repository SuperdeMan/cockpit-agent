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


# ─── ASR Provider（语音识别）───
# 官方文档：https://platform.xiaomimimo.com/docs/zh-CN/api/audio/Speech-Recognition

class BaseASRProvider:
    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        """returns (text, confidence, language, model_used, duration_ms)"""
        raise NotImplementedError


class MockASRProvider(BaseASRProvider):
    """无 API key 时的 ASR 兜底。"""
    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        return "[mock ASR] 语音识别结果（配置 LLM_API_KEY 后接入真实 ASR）", 0.0, language or "zh", "mock", 0


class MiMoASRProvider(BaseASRProvider):
    """小米 MiMo ASR API。

    与 LLM 共用同一 endpoint（/v1/chat/completions），音频通过 base64 data URI 传入。
    响应格式为 OpenAI chat.completion，识别文本在 choices[0].message.content。
    """
    BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        import base64
        import httpx

        # 音频编码为 base64 data URI
        mime = f"audio/{fmt or 'wav'}"
        b64 = base64.b64encode(audio).decode("ascii")
        data_uri = f"data:{mime};base64,{b64}"

        headers = {"api-key": self.api_key, "Content-Type": "application/json"}
        body = {
            "model": model or "mimo-v2.5-asr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_uri}},
                    ],
                }
            ],
            "asr_options": {"language": language or "auto"},
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=body)
            resp.raise_for_status()
            result = resp.json()

        # 响应：choices[0].message.content = 识别文本，usage.seconds = 音频秒数
        text = result["choices"][0]["message"]["content"]
        duration_sec = result.get("usage", {}).get("seconds", 0)
        return text, 0.9, language or "zh", model or "mimo-v2.5-asr", int(duration_sec * 1000)


def build_asr_provider() -> BaseASRProvider:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoASRProvider(api_key)
    return MockASRProvider()


# ─── TTS Provider（语音合成）───
# 官方文档：https://platform.xiaomimimo.com/docs/zh-CN/usage-guide/speech-synthesis-v2.5

class BaseTTSProvider:
    async def synthesize(self, text: str, voice_id: str, model: str,
                         speed: float, fmt: str):
        """returns (audio_bytes, format, duration_ms, model_used, voice_id)"""
        raise NotImplementedError

    async def list_voices(self, language: str, gender: str):
        """returns list of VoiceInfo dicts"""
        raise NotImplementedError


class MockTTSProvider(BaseTTSProvider):
    """无 API key 时的 TTS 兜底。"""
    async def synthesize(self, text: str, voice_id: str, model: str,
                         speed: float, fmt: str):
        return b"", fmt or "wav", 0, "mock", voice_id or "mimo_default"

    async def list_voices(self, language: str, gender: str):
        return [
            {"voice_id": "mock_voice", "name": "模拟音色", "language": "zh",
             "gender": "female", "description": "Mock 音色（配置 LLM_API_KEY 后接入真实 TTS）", "tags": ["默认"]},
        ]


class MiMoTTSProvider(BaseTTSProvider):
    """小米 MiMo TTS API。

    与 LLM 共用同一 endpoint（/v1/chat/completions）。
    目标文本放在 role=assistant 的 content 中，可选 role=user 传风格控制。
    响应：choices[0].message.audio.data 为 Base64 编码音频（wav/pcm16）。
    """
    BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"

    # 官方预置音色（mimo-v2.5-tts）
    VOICES = [
        {"voice_id": "mimo_default", "name": "MiMo-默认", "language": "zh",
         "gender": "neutral", "description": "中国集群默认冰糖", "tags": ["默认"]},
        {"voice_id": "冰糖", "name": "冰糖", "language": "zh",
         "gender": "female", "description": "中文女声", "tags": ["中文", "女声"]},
        {"voice_id": "茉莉", "name": "茉莉", "language": "zh",
         "gender": "female", "description": "中文女声", "tags": ["中文", "女声"]},
        {"voice_id": "苏打", "name": "苏打", "language": "zh",
         "gender": "male", "description": "中文男声", "tags": ["中文", "男声"]},
        {"voice_id": "白桦", "name": "白桦", "language": "zh",
         "gender": "male", "description": "中文男声", "tags": ["中文", "男声"]},
        {"voice_id": "Mia", "name": "Mia", "language": "en",
         "gender": "female", "description": "英文女声", "tags": ["英文", "女声"]},
        {"voice_id": "Chloe", "name": "Chloe", "language": "en",
         "gender": "female", "description": "英文女声", "tags": ["英文", "女声"]},
        {"voice_id": "Milo", "name": "Milo", "language": "en",
         "gender": "male", "description": "英文男声", "tags": ["英文", "男声"]},
        {"voice_id": "Dean", "name": "Dean", "language": "en",
         "gender": "male", "description": "英文男声", "tags": ["英文", "男声"]},
    ]

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def synthesize(self, text: str, voice_id: str, model: str,
                         speed: float, fmt: str):
        import base64
        import httpx

        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",  # 禁用 gzip，避免解码问题
        }
        out_fmt = "pcm16" if fmt == "pcm16" else "wav"
        body = {
            "model": model or "mimo-v2.5-tts",
            "messages": [
                {"role": "assistant", "content": text},
            ],
            "audio": {
                "format": out_fmt,
                "voice": voice_id or "mimo_default",
            },
        }
        # speed 参数：通过 role=user 的风格指令传入（如"语速稍快"）
        # 当前不注入 speed 到请求体，保持简洁；需要时可加 role=user content

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=body)
            resp.raise_for_status()
            # MiMo TTS 返回 JSON（含 base64 音频），手动解析避免编码问题
            raw = resp.content  # 原始字节（已自动解压 gzip）
            try:
                import json as _json
                result = _json.loads(raw)
                audio_b64 = result["choices"][0]["message"]["audio"]["data"]
                audio_bytes = base64.b64decode(audio_b64)
            except (ValueError, KeyError, IndexError, UnicodeDecodeError):
                # fallback：响应体直接是音频字节流
                audio_bytes = raw
        # 估算时长：PCM16 24kHz mono = 48000 bytes/sec
        bytes_per_sec = 48000 if out_fmt == "pcm16" else 48000  # WAV 头忽略
        duration_ms = int(len(audio_bytes) / bytes_per_sec * 1000) if audio_bytes else 0
        return audio_bytes, out_fmt, duration_ms, model or "mimo-v2.5-tts", voice_id or "mimo_default"

    async def list_voices(self, language: str, gender: str):
        voices = self.VOICES
        if language:
            lang_short = language[:2].lower()
            voices = [v for v in voices if v["language"] == lang_short or v["language"] == "neutral"]
        if gender:
            voices = [v for v in voices if v["gender"] == gender]
        return voices


def build_tts_provider() -> BaseTTSProvider:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoTTSProvider(api_key)
    return MockTTSProvider()
