"""LLM Gateway gRPC 服务：多模型路由 + 降级 + 缓存 + 限流 + 成本统计 + ASR/TTS。

Phase 1 已落地：缓存（messages 哈希）、令牌桶限流、token 成本统计。
Phase 1 扩展：ASR（MiMo mimo-v2.5-asr）+ TTS（MiMo mimo-v2.5-tts）+ 音色选择。
"""
from __future__ import annotations
import asyncio
import os
import time
import logging

import grpc
import httpx
from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
from cockpit.llm.v1 import audio_pb2, audio_pb2_grpc

from providers import build_asr_provider, build_tts_provider, ProviderHTTPError
from health import health_tracker
from llm_runtime import get_runtime

# 429 且带 Retry-After 时最多等这么久重试同模型一次；更长的 Retry-After 直接失败
# 让上层诚实降级（车内对话等不起）。
_429_WAIT_CAP_S = float(os.getenv("LLM_429_WAIT_CAP_S", "2"))
from cache import LLMCache
from ratelimit import RateLimiter
from metrics import cost_tracker
from observability.events import get_emitter

logger = logging.getLogger("llm.server")


class LLMGatewayServicer(llm_pb2_grpc.LLMGatewayServicer):
    def __init__(self):
        # 多 LLM 源：provider 注册表 + 全局 active 切换 + 档位解析统一收归 llm_runtime（gRPC 与
        # HTTP 控制端点共用同一进程内单例）。换/切服务商见 llm_runtime.py。
        self.runtime = get_runtime()
        self.cache = LLMCache(max_size=256, ttl_seconds=300)
        self.limiter = RateLimiter(global_rate=20, global_capacity=50)
        self.obs = get_emitter("llm-gateway")

    async def _emit_llm(self, request, *, model, latency_ms, cache_hit=False,
                        usage=(0, 0), status="ok", error="", thinking=None,
                        msgs=None, content=""):
        """obs.llm 事件（best-effort）：LLM 唯一出口在此收口，badcase 按 trace 回看每一跳。"""
        try:
            meta = dict(request.meta) if request.meta else {}
            await self.obs.emit_llm(
                trace_id=meta.get("trace_id", ""),
                session_id=meta.get("session_id", ""),
                caller=meta.get("caller_service") or meta.get("caller", ""),
                model=model,
                provider=self.runtime.active_id,          # 实际 serving 厂商（审计「哪个脑答的」）
                requested_tier=(request.model or ""),     # 调用方原始档位/模型参数
                pinned=False,                             # D2 请求级 pin 落地后按 meta 置真
                prompt_tokens=usage[0],
                completion_tokens=usage[1],
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                thinking=bool(thinking),
                status=status,
                error=error,
                prompt_tail=(msgs[-1].get("content", "") if msgs else ""),
                content_head=content,
            )
        except Exception:
            pass

    def _models(self, requested: str) -> list[str]:
        return self.runtime.resolve_models(requested)

    @staticmethod
    def _msgs(request):
        return [{"role": m.role, "content": m.content} for m in request.messages]

    @staticmethod
    def _thinking(request):
        """从 meta 读本次思考开关：``on``=开、``off``=关、缺省=None（用 provider 默认）。
        复杂任务（行程/调研）由编排层传 ``on``，结构化 JSON（Planner）不传/传 ``off``。"""
        v = dict(request.meta).get("thinking", "").lower() if request.meta else ""
        if v in ("on", "true", "1", "enabled"):
            return True
        if v in ("off", "false", "0", "disabled"):
            return False
        return None

    async def Complete(self, request, context):
        msgs = self._msgs(request)
        temp = request.temperature or 0.7
        max_tokens = request.max_tokens or 512
        thinking = self._thinking(request)

        # 限流
        caller = dict(request.meta).get("caller", "default")
        if not self.limiter.allow(caller):
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "rate limited")

        # 缓存查找（active provider + thinking 并入 key，避免切换/开关思考结果串味）
        models = self._models(request.model)
        aid = self.runtime.active_id
        cached = self.cache.get(msgs, f"{aid}:{models[0]}", temp, thinking)
        if cached:
            content, used, finish, usage = cached
            logger.debug("Cache hit")
            await self._emit_llm(request, model=used, latency_ms=0.0, cache_hit=True,
                                 usage=usage, thinking=thinking, msgs=msgs,
                                 content=content)
            return llm_pb2.CompleteResponse(
                content=content, model_used=used, finish_reason=finish,
                prompt_tokens=usage[0], completion_tokens=usage[1])

        # 调用（带降级：同厂商 primary→fast）。429 单独分类（D3）：Retry-After 小且预算
        # 余量足 → 等一次重试同模型；否则不再打 fast 档（限流通常是账号/厂商级，白打）。
        provider = self.runtime.active_provider()
        last_err = None
        rate_limited = False
        t_all = time.monotonic()
        for model in models:
            waited_429 = False
            while True:
                t0 = time.monotonic()
                try:
                    content, used, finish, usage = await provider.complete(
                        msgs, model, temp, max_tokens, thinking=thinking)
                    latency_ms = (time.monotonic() - t0) * 1000

                    # 写缓存
                    self.cache.put(msgs, f"{aid}:{model}", temp, content, used, thinking)

                    # 记录成本 + 健康
                    cost_tracker.record(used, usage[0], usage[1], latency_ms)
                    health_tracker.record(aid, True, latency_ms=latency_ms)
                    await self._emit_llm(request, model=used, latency_ms=latency_ms,
                                         usage=usage, thinking=thinking, msgs=msgs,
                                         content=content)

                    return llm_pb2.CompleteResponse(
                        content=content, model_used=used, finish_reason=finish,
                        prompt_tokens=usage[0], completion_tokens=usage[1])
                except Exception as e:
                    latency_ms = (time.monotonic() - t0) * 1000
                    cost_tracker.record(model, 0, 0, latency_ms, error=True)
                    last_err = e
                    if isinstance(e, ProviderHTTPError) and e.status_code == 429:
                        health_tracker.record(aid, False, kind="rate_limited", error=str(e))
                        ra = e.retry_after
                        remaining = context.time_remaining()
                        if (not waited_429 and ra is not None and ra <= _429_WAIT_CAP_S
                                and (remaining is None or remaining > ra + 2.0)):
                            waited_429 = True
                            logger.info("429 Retry-After=%.1fs，等待后重试 %s", ra, model)
                            await asyncio.sleep(ra)
                            continue          # 等一次重试同模型（仅一次）
                        rate_limited = True   # 跳过剩余档位
                        break
                    health_tracker.record(
                        aid, False,
                        kind="timeout" if isinstance(e, httpx.TimeoutException) else "",
                        error=str(e))
                    logger.warning("Model %s failed: %s; trying next", model, e)
                    break
            if rate_limited:
                break

        # 错误映射：429→RESOURCE_EXHAUSTED（SDK 对它不做重连重试——那是连接语义，白打）；
        # 上游超时 → DEADLINE_EXCEEDED（非 UNAVAILABLE），避免调用方 SDK 把它当瞬时错误重试
        # 一次致延迟翻倍（曾因此 info/trip 接地合成爆 step 预算）。连接级失败仍 UNAVAILABLE 供重试。
        await self._emit_llm(
            request, model=models[0],
            latency_ms=(time.monotonic() - t_all) * 1000,
            status=("rate_limited" if rate_limited
                    else "timeout" if isinstance(last_err, httpx.TimeoutException) else "err"),
            error=str(last_err), thinking=thinking, msgs=msgs)
        if rate_limited:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED,
                                f"provider rate limited (429): {last_err}")
        if isinstance(last_err, httpx.TimeoutException):
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "llm upstream timeout")
        # 请求性 4xx（400/403/413/422，含内容风控拒收）→ INVALID_ARGUMENT：同一被拒 prompt
        # 重连重试注定再拒，SDK 只对 UNAVAILABLE 做重试，避免白打第二遍（badcase a3fad033
        # 每次 new_sensitive 都成对出现即此）。
        req_4xx = (isinstance(last_err, ProviderHTTPError)
                   and last_err.status_code in (400, 403, 413, 422))
        err_text = str(last_err)
        if req_4xx or any(f"provider HTTP {c}" in err_text for c in (400, 403, 413, 422)):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT,
                                f"all models failed: {last_err}")
        await context.abort(grpc.StatusCode.UNAVAILABLE, f"all models failed: {last_err}")

    async def CompleteStream(self, request, context):
        msgs = self._msgs(request)
        models = self._models(request.model)
        thinking = self._thinking(request)
        aid = self.runtime.active_id

        # 流式不走缓存。**首 token 前**失败按档位链降级到下一模型（D4，兑现 R3.5 记录的
        # 「CompleteStream 无备用模型重试」缺口）；**首 token 后**不切——半段话术不可拼接，
        # 宁可 abort 让调用方走既有失败路径。
        last_err = None
        for model in models:
            t0 = time.monotonic()
            head: list[str] = []
            head_len = 0
            first_token = False
            try:
                async for delta in self.runtime.active_provider().stream(
                        msgs, model, request.temperature or 0.7, request.max_tokens or 512,
                        thinking=thinking):
                    if delta:
                        first_token = True
                        if head_len < 800:  # 观测只留输出头部，不为观测缓冲全文
                            head.append(delta)
                            head_len += len(delta)
                    yield llm_pb2.CompleteChunk(delta=delta, done=False)
                yield llm_pb2.CompleteChunk(delta="", done=True)
                latency_ms = (time.monotonic() - t0) * 1000
                cost_tracker.record(model, 0, 0, latency_ms)
                health_tracker.record(aid, True, latency_ms=latency_ms)
                await self._emit_llm(request, model=model, latency_ms=latency_ms,
                                     thinking=thinking, msgs=msgs, content="".join(head))
                return
            except Exception as e:
                latency_ms = (time.monotonic() - t0) * 1000
                cost_tracker.record(model, 0, 0, latency_ms, error=True)
                kind = ("rate_limited"
                        if isinstance(e, ProviderHTTPError) and e.status_code == 429
                        else "timeout" if isinstance(e, httpx.TimeoutException) else "")
                health_tracker.record(aid, False, kind=kind, error=str(e))
                last_err = e
                if first_token:   # 已流出内容：不可换模型拼接，按既有语义直接失败
                    await self._emit_llm(request, model=model, latency_ms=latency_ms,
                                         status="err", error=str(e), thinking=thinking,
                                         msgs=msgs)
                    code = (grpc.StatusCode.DEADLINE_EXCEEDED
                            if isinstance(e, httpx.TimeoutException)
                            else grpc.StatusCode.UNAVAILABLE)
                    await context.abort(code, str(e))
                logger.warning("stream model %s failed before first token: %s; trying next",
                               model, e)

        await self._emit_llm(request, model=models[0], latency_ms=0.0,
                             status="err", error=str(last_err), thinking=thinking, msgs=msgs)
        if isinstance(last_err, ProviderHTTPError) and last_err.status_code == 429:
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED,
                                f"provider rate limited (429): {last_err}")
        code = (grpc.StatusCode.DEADLINE_EXCEEDED
                if isinstance(last_err, httpx.TimeoutException)
                else grpc.StatusCode.UNAVAILABLE)
        await context.abort(code, str(last_err))

    async def Embed(self, request, context):
        """文本向量化（记忆语义检索）。provider 不支持/失败 → UNAVAILABLE，调用方降级。"""
        texts = list(request.texts)
        if not texts:
            return llm_pb2.EmbedResponse(embeddings=[], dim=0)
        model = request.model or os.getenv("LLM_EMBED_MODEL", "")
        try:
            vecs = await self.runtime.embed_provider().embed(texts, model)
        except NotImplementedError:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "provider 不支持 embedding")
            return
        except Exception as e:
            logger.warning("Embed failed: %s", e)
            await context.abort(grpc.StatusCode.UNAVAILABLE, f"embed: {e}")
            return
        dim = len(vecs[0]) if vecs and vecs[0] else 0
        return llm_pb2.EmbedResponse(
            embeddings=[llm_pb2.Embedding(values=v) for v in vecs],
            model_used=model or "default", dim=dim)


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
