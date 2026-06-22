"""LLM Gateway gRPC 服务：多模型路由 + 降级 + 缓存 + 限流 + 成本统计 + ASR/TTS。

Phase 1 已落地：缓存（messages 哈希）、令牌桶限流、token 成本统计。
Phase 1 扩展：ASR（MiMo mimo-v2.5-asr）+ TTS（MiMo mimo-v2.5-tts）+ 音色选择。
"""
from __future__ import annotations
import os
import time
import logging

import grpc
from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from cockpit.llm.v1 import audio_pb2, audio_pb2_grpc

from providers import build_provider, build_asr_provider, build_tts_provider
from cache import LLMCache
from ratelimit import RateLimiter
from metrics import cost_tracker

logger = logging.getLogger("llm.server")


class LLMGatewayServicer(llm_pb2_grpc.LLMGatewayServicer):
    def __init__(self):
        self.provider = build_provider()
        # 默认对齐项目约定（.env.example/compose/conventions.md 均为 MiMo）；部署经 env 覆盖。
        # 不再默认 claude——避免漏配 env 时把 claude 模型名发给已配置的 MiMo provider 而报错。
        self.primary = os.getenv("LLM_MODEL_PRIMARY", "mimo-v2.5-pro")
        self.fallback = os.getenv("LLM_MODEL_FALLBACK", "")
        self.cache = LLMCache(max_size=256, ttl_seconds=300)
        self.limiter = RateLimiter(global_rate=20, global_capacity=50)

    def _models(self, requested: str) -> list[str]:
        if requested:
            return [requested]
        return [m for m in (self.primary, self.fallback) if m] or ["mock"]

    @staticmethod
    def _msgs(request):
        return [{"role": m.role, "content": m.content} for m in request.messages]

    async def Complete(self, request, context):
        msgs = self._msgs(request)
        temp = request.temperature or 0.7
        max_tokens = request.max_tokens or 512

        # 限流
        caller = dict(request.meta).get("caller", "default")
        if not self.limiter.allow(caller):
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "rate limited")

        # 缓存查找
        cached = self.cache.get(msgs, request.model or self.primary, temp)
        if cached:
            content, used, finish, usage = cached
            logger.debug("Cache hit")
            return llm_pb2.CompleteResponse(
                content=content, model_used=used, finish_reason=finish,
                prompt_tokens=usage[0], completion_tokens=usage[1])

        # 调用（带降级）
        last_err = None
        for model in self._models(request.model):
            t0 = time.monotonic()
            try:
                content, used, finish, usage = await self.provider.complete(
                    msgs, model, temp, max_tokens)
                latency_ms = (time.monotonic() - t0) * 1000

                # 写缓存
                self.cache.put(msgs, model, temp, content, used)

                # 记录成本
                cost_tracker.record(used, usage[0], usage[1], latency_ms)

                return llm_pb2.CompleteResponse(
                    content=content, model_used=used, finish_reason=finish,
                    prompt_tokens=usage[0], completion_tokens=usage[1])
            except Exception as e:
                latency_ms = (time.monotonic() - t0) * 1000
                cost_tracker.record(model, 0, 0, latency_ms, error=True)
                last_err = e
                logger.warning("Model %s failed: %s; trying next", model, e)

        await context.abort(grpc.StatusCode.UNAVAILABLE, f"all models failed: {last_err}")

    async def CompleteStream(self, request, context):
        msgs = self._msgs(request)
        model = self._models(request.model)[0]

        # 流式不走缓存
        t0 = time.monotonic()
        try:
            async for delta in self.provider.stream(
                    msgs, model, request.temperature or 0.7, request.max_tokens or 512):
                yield llm_pb2.CompleteChunk(delta=delta, done=False)
            yield llm_pb2.CompleteChunk(delta="", done=True)
            latency_ms = (time.monotonic() - t0) * 1000
            cost_tracker.record(model, 0, 0, latency_ms)
        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            cost_tracker.record(model, 0, 0, latency_ms, error=True)
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(e))


class AudioServiceServicer(audio_pb2_grpc.AudioServiceServicer):
    """ASR + TTS 服务：语音识别与合成，支持音色选择。"""

    def __init__(self):
        self.asr = build_asr_provider()
        self.tts = build_tts_provider()

    async def Transcribe(self, request, context):
        t0 = time.monotonic()
        try:
            text, conf, lang, model_used, dur = await self.asr.transcribe(
                audio=request.audio,
                fmt=request.format or "wav",
                language=request.language or "zh",
                model=request.model or "",
            )
            latency_ms = (time.monotonic() - t0) * 1000
            logger.info("ASR: %d bytes -> %d chars (%.0fms)", len(request.audio), len(text), latency_ms)
            return audio_pb2.TranscribeResponse(
                text=text, confidence=conf, language=lang,
                model_used=model_used, duration_ms=dur,
            )
        except Exception as e:
            logger.warning("ASR failed: %s", e)
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"ASR failed: {e}")

    async def Synthesize(self, request, context):
        t0 = time.monotonic()
        try:
            audio_bytes, fmt, dur, model_used, voice = await self.tts.synthesize(
                text=request.text,
                voice_id=request.voice_id or "",
                model=request.model or "",
                speed=request.speed or 1.0,
                fmt=request.format or "mp3",
            )
            latency_ms = (time.monotonic() - t0) * 1000
            logger.info("TTS: %d chars -> %d bytes, voice=%s (%.0fms)",
                        len(request.text), len(audio_bytes), voice, latency_ms)
            return audio_pb2.SynthesizeResponse(
                audio=audio_bytes, format=fmt, duration_ms=dur,
                model_used=model_used, voice_id=voice,
            )
        except Exception as e:
            logger.warning("TTS failed: %s", e)
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"TTS failed: {e}")

    async def ListVoices(self, request, context):
        voices = await self.tts.list_voices(
            language=request.language or "",
            gender=request.gender or "",
        )
        return audio_pb2.ListVoicesResponse(
            voices=[audio_pb2.VoiceInfo(**v) for v in voices],
        )
