"""LLM Provider 抽象与实现。**更换服务商优先改 env，无需改代码**。

- anthropic：Anthropic Claude API（独立 SDK）
- 其余（xiaomimimo/openai/deepseek/qwen/自建 vLLM…）：统一走 OpenAI 兼容 HTTP provider，
  端点 `LLM_BASE_URL`、鉴权 `LLM_AUTH_STYLE`、思考开关 `LLM_DISABLE_THINKING` 全经 env 注入。
- mock：无 `LLM_API_KEY` 时的回显兜底（PoC 可离线端到端）。

仅当需要一种全新的非 OpenAI 兼容协议时，才在此新增 Provider 类。
"""
from __future__ import annotations
import asyncio
import json
import os

import httpx

# ── 出站 HTTP 连接池 + 超时（复用连接，免去每调用新建 client 的 TLS 握手开销）──
_HTTP_LIMITS = httpx.Limits(max_connections=32, max_keepalive_connections=16,
                            keepalive_expiry=30.0)
_HTTP_CONNECT_S = float(os.getenv("LLM_HTTP_CONNECT_S", "5") or 5)
_HTTP_READ_CAP_S = float(os.getenv("LLM_HTTP_READ_CAP_S", "75") or 75)   # complete 兜底上限
_STREAM_STALL_S = float(os.getenv("LLM_STREAM_STALL_S", "30") or 30)     # 流式 per-chunk 静默上限
_EMBED_READ_CAP_S = float(os.getenv("LLM_EMBED_READ_CAP_S", "25") or 25)


def _read_budget(budget_s, cap_s: float) -> float:
    """上游 read 超时：有调用方 deadline（gRPC context.time_remaining）时取其 90% 收进
    窗口内——网关先于调用方失败、返回干净错误，而非被调用方中途取消（"无响应"）；
    无 deadline 时用 cap 兜底。"""
    try:
        b = float(budget_s) if budget_s is not None else 0.0
    except (TypeError, ValueError):
        b = 0.0
    if b > 0:
        return max(1.0, min(cap_s, b * 0.9))
    return cap_s


def _http_timeout(budget_s, read_cap: float) -> httpx.Timeout:
    return httpx.Timeout(_read_budget(budget_s, read_cap),
                         connect=min(_HTTP_CONNECT_S, read_cap), pool=5.0)


class BaseProvider:
    async def complete(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        """returns (content, model_used, finish_reason, (prompt_tokens, completion_tokens)).

        thinking: None=用服务商默认（env LLM_DISABLE_THINKING）；True=本次开思考；
        False=本次关思考。复杂任务（行程/调研）由编排层经 meta 动态传 True。
        """
        raise NotImplementedError

    async def stream(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, texts, model="", timeout_s=None):
        """returns list[list[float]]（与 texts 一一对应）。默认未实现，由子类提供。"""
        raise NotImplementedError


_EMBED_DIM = 384  # 与 memory.memory_item.embedding vector(384) 对齐


def _mock_embed_one(text: str) -> list[float]:
    """确定性伪向量（非语义，仅供无 key/降级时打通 pgvector 链路与测试）。"""
    import hashlib
    h = hashlib.sha256((text or "").encode()).digest()
    return [(h[i % len(h)] / 128.0) - 1.0 for i in range(_EMBED_DIM)]


class MockProvider(BaseProvider):
    """无 API key 时的兜底，保证 PoC 可离线端到端跑通。"""
    async def complete(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        text = f"[mock] 我听到你说「{user}」。配置 LLM_API_KEY 后即可接入真实模型。"
        return text, "mock", "stop", (0, 0)

    async def stream(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        content, *_ = await self.complete(messages, model, temperature, max_tokens)
        for ch in content:
            yield ch

    async def embed(self, texts, model="", timeout_s=None):
        return [_mock_embed_one(t) for t in texts]


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

    async def complete(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        # thinking 形参保持签名一致；Anthropic extended thinking 暂未接线（目标服务商是 MiMo）。
        system, msgs = self._split(messages)
        resp = await self.client.messages.create(
            model=model, system=system, messages=msgs,
            temperature=temperature, max_tokens=max_tokens or 512)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, model, resp.stop_reason, (resp.usage.input_tokens, resp.usage.output_tokens)

    async def stream(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        system, msgs = self._split(messages)
        async with self.client.messages.stream(
                model=model, system=system, messages=msgs,
                temperature=temperature, max_tokens=max_tokens or 512) as s:
            async for text in s.text_stream:
                yield text


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI 兼容 Chat Completions 提供商（MiMo / OpenAI / DeepSeek / Qwen / 本地 vLLM 等）。

    端点、鉴权、思考开关全部经配置注入——**更换 LLM 服务商只改 env、不动代码**：
    - LLM_BASE_URL：chat/completions 完整 URL（默认小米 MiMo）
    - LLM_AUTH_STYLE：``api-key``（默认，MiMo）| ``bearer``（多数 OpenAI 兼容服务）
    - LLM_DISABLE_THINKING：``true``（默认，MiMo 推理模型须关思考保结构化输出）| ``false``

    MiMo docs: https://platform.xiaomimimo.com/docs/zh-CN/quick-start/first-api-call
    """
    _DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"

    def __init__(self, api_key: str, base_url: str = "",
                 auth_style: str = "api-key", disable_thinking: bool = True,
                 embed_url: str = "", embed_model: str = "", embed_api_key: str = "",
                 embed_auth_style: str = "bearer", embed_dimensions: int = 0):
        self.api_key = api_key
        self.base_url = base_url or self._DEFAULT_BASE_URL
        self.auth_style = (auth_style or "api-key").lower()
        self.disable_thinking = disable_thinking
        # 向量化（embedding）端点/鉴权/维度独立于 chat——embedding 常用另一服务商（如百炼）。
        # 默认从 chat 端点推导；embed_api_key 缺省回退 chat key；auth 默认 bearer（OpenAI 风格）。
        self.embed_url = embed_url or self.base_url.replace("/chat/completions", "/embeddings")
        self.embed_model = embed_model
        self.embed_api_key = embed_api_key or api_key
        self.embed_auth_style = (embed_auth_style or "bearer").lower()
        self.embed_dimensions = int(embed_dimensions or 0)
        self._client: httpx.AsyncClient | None = None  # 复用的出站连接池（懒建，绑定运行 loop）

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(limits=_HTTP_LIMITS)
        return self._client

    def _embed_headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.embed_auth_style == "api-key":
            h["api-key"] = self.embed_api_key
        else:
            h["Authorization"] = f"Bearer {self.embed_api_key}"
        return h

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.auth_style == "bearer":
            h["Authorization"] = f"Bearer {self.api_key}"
        else:  # 默认 MiMo 风格
            h["api-key"] = self.api_key
        return h

    def _resolve_thinking(self, thinking) -> bool:
        """本次调用是否关思考：thinking=None 用构造默认；True/False 覆盖本次。"""
        return self.disable_thinking if thinking is None else (not thinking)

    async def complete(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        disable = self._resolve_thinking(thinking)
        # 开思考时给足 token：reasoning 占预算，content 容易被饿空/截断；下限抬到 2048。
        max_out = (max_tokens or 512) if disable else max((max_tokens or 512), 2048)
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_out,
            "stream": False,
        }
        if disable:
            # MiMo 等推理模型：默认把 token 预算几乎全花在 reasoning_content 上，
            # 导致结构化任务（Planner JSON、聚合改写、接地合成）的 content 被饿成空/截断。
            # 关闭思考即可拿到干净、确定、低延迟的 content。非推理服务商可经 env 关掉本项。
            # 开思考时不发本键（回 MiMo 原生思考态），reasoning_content 留服务端、不取不下发。
            body["thinking"] = {"type": "disabled"}
        resp = await self._get_client().post(
            self.base_url, headers=self._headers(), json=body,
            timeout=_http_timeout(timeout_s, _HTTP_READ_CAP_S))
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        return content, model, "stop", (prompt_tokens, completion_tokens)

    async def stream(self, messages, model, temperature, max_tokens, thinking=None, timeout_s=None):
        disable = self._resolve_thinking(thinking)
        max_out = (max_tokens or 512) if disable else max((max_tokens or 512), 2048)
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_out,
            "stream": True,
        }
        if disable:
            body["thinking"] = {"type": "disabled"}
        # 流式：read 超时作 per-chunk stall 检测（无新 chunk 超时即中止），不让上游卡死吊死整链。
        stall = _read_budget(timeout_s, _STREAM_STALL_S)
        async with self._get_client().stream(
                "POST", self.base_url, headers=self._headers(), json=body,
                timeout=httpx.Timeout(stall, connect=_HTTP_CONNECT_S, pool=5.0)) as resp:
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
                    # 只取 content；reasoning_content（思考增量）刻意丢弃，不下发给用户。
                    text = delta.get("content", "")
                    if text:
                        yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


    async def embed(self, texts, model="", timeout_s=None):
        """OpenAI 兼容 /embeddings（百炼 text-embedding-v4 等）。返回 list[list[float]]。"""
        body = {"model": model or self.embed_model or "text-embedding-v4",
                "input": list(texts)}
        if self.embed_dimensions:  # v3/v4 支持指定输出维度（须与 memory EMBED_DIM 一致）
            body["dimensions"] = self.embed_dimensions
            body["encoding_format"] = "float"
        resp = await self._get_client().post(
            self.embed_url, headers=self._embed_headers(), json=body,
            timeout=_http_timeout(timeout_s, _EMBED_READ_CAP_S))
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return [list(d["embedding"]) for d in items]


# 向后兼容别名（历史代码/测试可能引用 MiMoProvider）
MiMoProvider = OpenAICompatibleProvider


def build_provider() -> BaseProvider:
    """按 env 装配 LLM provider。换服务商只改 env：

    - LLM_PROVIDER：``anthropic`` 走 Claude SDK；其余（xiaomimimo/mimo/openai/deepseek/
      qwen/自建…）一律走 OpenAI 兼容 HTTP provider，端点/鉴权/思考开关见下。
    - LLM_API_KEY / LLM_BASE_URL / LLM_AUTH_STYLE / LLM_DISABLE_THINKING
    无 key → MockProvider（PoC 可离线跑通）。
    """
    provider = os.getenv("LLM_PROVIDER", "xiaomimimo").lower()
    api_key = os.getenv("LLM_API_KEY", "")

    if not api_key:
        print(f"[llm-gateway] provider={provider}, no API key -> MockProvider", flush=True)
        return MockProvider()

    if provider == "anthropic":
        return AnthropicProvider(api_key)

    # 其余一律 OpenAI 兼容：端点/鉴权/思考开关经 env 注入，新增服务商无需改代码
    return OpenAICompatibleProvider(
        api_key,
        base_url=os.getenv("LLM_BASE_URL", ""),
        auth_style=os.getenv("LLM_AUTH_STYLE", "api-key"),
        disable_thinking=os.getenv("LLM_DISABLE_THINKING", "true").lower() != "false",
        embed_url=os.getenv("LLM_EMBED_URL", ""),
        embed_model=os.getenv("LLM_EMBED_MODEL", ""),
        embed_api_key=os.getenv("LLM_EMBED_API_KEY", ""),
        embed_auth_style=os.getenv("LLM_EMBED_AUTH_STYLE", "bearer"),
        embed_dimensions=int(os.getenv("LLM_EMBED_DIMENSIONS", "0") or 0),
    )


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
        self._client: httpx.AsyncClient | None = None

    async def transcribe(self, audio: bytes, fmt: str, language: str, model: str):
        import base64

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

        if self._client is None:
            self._client = httpx.AsyncClient(limits=_HTTP_LIMITS)
        resp = await self._client.post(self.BASE_URL, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        result = resp.json()

        # 响应：choices[0].message.content = 识别文本，usage.seconds = 音频秒数
        text = result["choices"][0]["message"]["content"]
        duration_sec = result.get("usage", {}).get("seconds", 0)
        return text, 0.9, language or "zh", model or "mimo-v2.5-asr", int(duration_sec * 1000)


def build_asr_provider() -> BaseASRProvider:
    provider = os.getenv("LLM_PROVIDER", "xiaomimimo").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoASRProvider(api_key)
    return MockASRProvider()


# ─── 流式 ASR Provider（实时识别上屏）───
# 设计见 docs/design/2026-06-30-asr-streaming-design.md。
# 传输层（HMI↔网关 WS + 网关流式 ffmpeg）在 http_server.py；此处是
# "16k mono PCM16 帧流 → {text, final} 文本流" 的引擎抽象，引擎经 env/请求可换。

def _wav_header(pcm_len: int, sr: int = 16000) -> bytes:
    """给裸 PCM16 mono 加 44 字节 WAV 头（供 MiMo 批 ASR 当 wav 用）。"""
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + pcm_len) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
            + b"data" + struct.pack("<I", pcm_len))


class BaseStreamingASRProvider:
    async def stream(self, pcm_chunks, *, language: str = "zh"):
        """pcm_chunks: 16kHz mono s16le PCM 帧的异步迭代器；yield {'text': str, 'final': bool}。"""
        raise NotImplementedError
        yield  # noqa: 标记为 async generator（永不到达）


class MiMoChunkedASRProvider(BaseStreamingASRProvider):
    """回退引擎：累积 PCM、每 ~interval 秒封 WAV 打一次 MiMo 批 ASR 产伪 partial。
    用已验证可用的 MiMo 批 ASR，保证上屏功能在 DashScope 不可用时也跑通。"""

    def __init__(self, batch: "MiMoASRProvider", model: str = "", interval_s: float = 1.2):
        self.batch = batch
        self.model = model or os.getenv("ASR_MODEL", "mimo-v2.5-asr")
        self.interval_s = interval_s

    async def stream(self, pcm_chunks, *, language="zh"):
        import time
        buf = bytearray()
        last_t = 0.0
        last_text = ""

        async def transcribe_now() -> str:
            if len(buf) < 3200:  # <0.1s 不值得打
                return last_text
            wav = _wav_header(len(buf)) + bytes(buf)
            text, *_ = await self.batch.transcribe(audio=wav, fmt="wav", language=language, model=self.model)
            return (text or "").strip()

        async for chunk in pcm_chunks:
            buf.extend(chunk)
            now = time.monotonic()
            if now - last_t >= self.interval_s:
                last_t = now
                try:
                    t = await transcribe_now()
                    if t and t != last_text:
                        last_text = t
                        yield {"text": t, "final": False}
                except Exception:
                    pass  # 中途失败不影响整段定稿
        try:
            final = await transcribe_now()
        except Exception:
            final = last_text
        yield {"text": (final or last_text), "final": True}


class DashScopeRealtimeASRProvider(BaseStreamingASRProvider):
    """DashScope（百炼）实时 ASR——OpenAI 兼容 Realtime 协议（实测见设计 §2.1）。
    端点 wss://…/api-ws/v1/realtime?model=<id>，Bearer 鉴权。
    session.created → session.update → input_audio_buffer.append(base64 PCM16) →
    （流末）commit → conversation.item.input_audio_transcription.delta(partial)/.completed(final)。"""

    def __init__(self, api_key: str, ws_url: str, model: str):
        self.api_key = api_key
        self.ws_url = ws_url
        self.model = model

    async def stream(self, pcm_chunks, *, language="zh"):
        import base64
        import aiohttp
        url = f"{self.ws_url}?model={self.model}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url, headers={"Authorization": f"Bearer {self.api_key}"}, heartbeat=20.0,
            ) as ws:
                # 等 session.created（容错读几条）
                for _ in range(5):
                    msg = await ws.receive(timeout=10)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        if json.loads(msg.data).get("type") == "session.created":
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                        raise RuntimeError("dashscope ws closed before session.created")
                _eid = [0]

                def _ev(o):
                    _eid[0] += 1
                    return {"event_id": f"ev{_eid[0]}", **o}

                # 实测协议（DashScope 文档）：format=pcm（非 pcm16）、transcription.language（非 model）、
                # 手动模式（turn_detection=None）append→commit；中间结果 .text(text+stash)、定稿 .completed(transcript)。
                await ws.send_json(_ev({"type": "session.update", "session": {
                    "input_audio_format": "pcm", "sample_rate": 16000,
                    "input_audio_transcription": {"language": language or "zh"},
                    "turn_detection": None,
                }}))

                async def pump():
                    try:
                        async for chunk in pcm_chunks:
                            await ws.send_json(_ev({"type": "input_audio_buffer.append",
                                                    "audio": base64.b64encode(chunk).decode("ascii")}))
                        await ws.send_json(_ev({"type": "input_audio_buffer.commit"}))
                    except Exception:
                        pass

                pump_task = asyncio.create_task(pump())
                acc = ""
                final_sent = False
                try:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        m = json.loads(msg.data)
                        t = m.get("type", "")
                        if "input_audio_transcription" in t and t.endswith(".text"):
                            acc = (m.get("text") or "") + (m.get("stash") or "")  # 已确认 + 草稿后缀
                            if acc:
                                yield {"text": acc, "final": False}
                        elif "input_audio_transcription" in t and t.endswith("completed"):
                            acc = (m.get("transcript") or acc)
                            yield {"text": acc, "final": True}
                            final_sent = True
                            break
                        elif t == "error":
                            raise RuntimeError((m.get("error") or {}).get("message", "dashscope asr error"))
                finally:
                    pump_task.cancel()
                if not final_sent:
                    # 异常关闭/无转写（如服务端 1011 InternalError）→ 抛错让网关回 error、
                    # HMI 无感回退批处理；有半截 partial 则当定稿用。
                    if not acc:
                        raise RuntimeError(f"dashscope 实时无转写 (close_code={ws.close_code})")
                    yield {"text": acc, "final": True}


def build_streaming_asr_provider(provider: str = "", model: str = "") -> "BaseStreamingASRProvider | None":
    """按请求/env 选流式引擎。provider/model 为请求级覆盖（HMI 设置可切），空则用 env 默认。
    无可用 key 或 off → None（HMI 探测到则无感回退批处理 /api/asr）。"""
    provider = (provider or os.getenv("ASR_STREAM_PROVIDER", "mimo-chunked")).strip().lower()
    if provider in ("", "off", "none"):
        return None
    if provider in ("dashscope", "dashscope-qwen3", "dashscope-fun", "qwen3", "fun"):
        key = os.getenv("DASHSCOPE_ASR_KEY") or os.getenv("LLM_EMBED_API_KEY", "")
        if not key:
            return None
        ws_url = os.getenv("DASHSCOPE_ASR_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
        mdl = model or os.getenv("ASR_STREAM_MODEL", "Qwen3-ASR-Flash-Realtime-2026-02-10")
        if provider in ("fun", "dashscope-fun") and not model:
            mdl = "fun-asr-realtime"
        return DashScopeRealtimeASRProvider(key, ws_url, mdl)
    if provider in ("mimo", "mimo-chunked", "mimo-batch"):
        api_key = os.getenv("LLM_API_KEY", "")
        if not api_key:
            return None
        return MiMoChunkedASRProvider(MiMoASRProvider(api_key), model=model)
    return None


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
        self._client: httpx.AsyncClient | None = None

    async def synthesize(self, text: str, voice_id: str, model: str,
                         speed: float, fmt: str):
        import base64

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

        if self._client is None:
            self._client = httpx.AsyncClient(limits=_HTTP_LIMITS)
        resp = await self._client.post(self.BASE_URL, headers=headers, json=body, timeout=60)
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
    provider = os.getenv("LLM_PROVIDER", "xiaomimimo").lower()
    api_key = os.getenv("LLM_API_KEY", "")
    if provider in ("xiaomimimo", "mimo") and api_key:
        return MiMoTTSProvider(api_key)
    return MockTTSProvider()
