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

class BaseASRProvider:
    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        """returns (text, confidence, language, model_used, duration_ms)"""
        raise NotImplementedError


class MockASRProvider(BaseASRProvider):
    """无 API key 时的 ASR 兜底。"""
    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        return "[mock ASR] 语音识别结果（配置 LLM_API_KEY 后接入真实 ASR）", 0.0, language or "zh", "mock", 0


class MiMoASRProvider(BaseASRProvider):
    """小米 MiMo ASR API（OpenAI Whisper 兼容格式）。

    endpoint: https://token-plan-cn.xiaomimimo.com/v1/audio/transcriptions
    auth: api-key header
    """
    BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1/audio/transcriptions"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        import httpx
        headers = {"api-key": self.api_key}
        # multipart/form-data 上传音频
        files = {"file": (f"audio.{fmt or 'wav'}", audio, f"audio/{fmt or 'wav'}")}
        data = {
            "model": model or "mimo-v2.5-asr",
            "language": language or "zh",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.BASE_URL, headers=headers, files=files, data=data)
            resp.raise_for_status()
            result = resp.json()

        text = result.get("text", "")
        language_detected = result.get("language", language or "zh")
        duration = result.get("duration", 0)
        return text, 0.9, language_detected, model or "mimo-v2.5-asr", int(duration * 1000)


def build_asr_provider() -> BaseASRProvider:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoASRProvider(api_key)
    return MockASRProvider()


# ─── TTS Provider（语音合成）───

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
        # 返回空音频（实际场景应返回静音 PCM）
        return b"", fmt or "mp3", 0, "mock", voice_id or "default"

    async def list_voices(self, language: str, gender: str):
        return [
            {"voice_id": "mock_voice", "name": "模拟音色", "language": "zh",
             "gender": "female", "description": "Mock 音色（配置 LLM_API_KEY 后接入真实 TTS）", "tags": ["默认"]},
        ]


class MiMoTTSProvider(BaseTTSProvider):
    """小米 MiMo TTS API（OpenAI 兼容格式）。

    endpoint: https://token-plan-cn.xiaomimimo.com/v1/audio/speech
    auth: api-key header
    音色参考：https://platform.xiaomimimo.com/docs/zh-CN/usage-guide/speech-synthesis-v2.5
    """
    BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1/audio/speech"

    # MiMo TTS 支持的音色列表（2.5 版本）
    VOICES = [
        {"voice_id": "zhifeng_emo", "name": "知枫", "language": "zh",
         "gender": "male", "description": "温暖磁性，适合对话", "tags": ["温暖", "磁性", "对话"]},
        {"voice_id": "zhixiaomo_emo", "name": "知小墨", "language": "zh",
         "gender": "female", "description": "活泼可爱，适合闲聊", "tags": ["活泼", "可爱", "闲聊"]},
        {"voice_id": "zhishu_emo", "name": "知树", "language": "zh",
         "gender": "male", "description": "沉稳大气，适合播报", "tags": ["沉稳", "大气", "播报"]},
        {"voice_id": "zhimei_emo", "name": "知美", "language": "zh",
         "gender": "female", "description": "温柔优雅，适合服务", "tags": ["温柔", "优雅", "服务"]},
        {"voice_id": "zhiyun_emo", "name": "知云", "language": "zh",
         "gender": "female", "description": "清新自然，适合导航", "tags": ["清新", "自然", "导航"]},
        {"voice_id": "zhigang_emo", "name": "知刚", "language": "zh",
         "gender": "male", "description": "浑厚有力，适合车控", "tags": ["浑厚", "有力", "车控"]},
    ]

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def synthesize(self, text: str, voice_id: str, model: str,
                         speed: float, fmt: str):
        import httpx
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "model": model or "mimo-v2.5-tts",
            "input": text,
            "voice": voice_id or "zhifeng_emo",
            "speed": speed or 1.0,
            "response_format": fmt or "mp3",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self.BASE_URL, headers=headers, json=body)
            resp.raise_for_status()
            audio = resp.read()

        # 估算时长（粗略：MP3 约 16kbps）
        bitrate = 16000 if (fmt or "mp3") == "mp3" else 128000
        duration_ms = int(len(audio) * 8 / bitrate * 1000) if audio else 0
        return audio, fmt or "mp3", duration_ms, model or "mimo-v2.5-tts", voice_id or "zhifeng_emo"

    async def list_voices(self, language: str, gender: str):
        voices = self.VOICES
        if language:
            voices = [v for v in voices if v["language"] == language]
        if gender:
            voices = [v for v in voices if v["gender"] == gender]
        return voices


def build_tts_provider() -> BaseTTSProvider:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoTTSProvider(api_key)
    return MockTTSProvider()
