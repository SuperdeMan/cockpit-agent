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
from google.protobuf.json_format import MessageToDict
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


class FrameUnavailable(RuntimeError):
    """M4 P4：请求声明了 vision_frame_id 但帧已过期/不存在。

    **这是错误，不是「那就只发文字吧」**——静默降级会让 VL 模型对着空气答
    「看不清，画面有点模糊」（真栈 e2e ⑤ 实测原话），它在假装看到了一张模糊的图。
    那比说不出更糟：用户没有任何办法判断真假。故显式 FAILED_PRECONDITION，
    由调用方（vision Agent）诚实说「画面已经过期了，再问我一次」。
    """

    def __init__(self, frame_id: str):
        super().__init__(f"vision frame unavailable (expired or unknown): {frame_id}")


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
                        msgs=None, content="", provider="", pinned=False):
        """obs.llm 事件（best-effort）：LLM 唯一出口在此收口，badcase 按 trace 回看每一跳。"""
        try:
            meta = dict(request.meta) if request.meta else {}
            await self.obs.emit_llm(
                trace_id=meta.get("trace_id", ""),
                session_id=meta.get("session_id", ""),
                caller=meta.get("caller_service") or meta.get("caller", ""),
                model=model,
                provider=provider or self.runtime.active_id,  # 实际 serving 厂商（审计「哪个脑答的」）
                requested_tier=(request.model or ""),         # 调用方原始档位/模型参数
                pinned=pinned,                                # 请求级 pin（D2）
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

    def _serving(self, request):
        """请求级 pin（meta.llm_provider/llm_model，运行时硬化 D2）：返回
        (provider, pid, models, pinned)。无 pin → active 现状；pin 到未配置厂商 →
        ValueError，调用方映射 INVALID_ARGUMENT fail-closed——pin 的意义就是不许静默漂移。"""
        meta = dict(request.meta) if request.meta else {}
        pin = (meta.get("llm_provider") or "").strip().lower()
        if not pin:
            return (self.runtime.active_provider(), self.runtime.active_id,
                    self.runtime.resolve_models(request.model), False)
        entry = self.runtime.provider_entry(pin)
        if entry is None:
            raise ValueError(f"pinned llm_provider 未配置或不可用: {pin}")
        pid, provider = entry
        models = self.runtime.resolve_models_for(
            pid, request.model, (meta.get("llm_model") or "").strip())
        return provider, pid, models, True

    def _backup_serving(self, aid: str, pinned: bool, tools_spec) -> tuple | None:
        """跨厂商备份档（`LLM_BACKUP=provider[:model]`，如 `deepseek:deepseek-v4-flash`）。

        active 厂商**整链**耗尽后兜底一跳——含 429：限流是账号/厂商级，同厂商换档
        白打，换厂商正是对症（2026-08-15 实测 MiniMax 上游抖动时厂商内 fast=primary
        同名，一抖即整请求死）。三条边界：
        - **pinned 请求恒 None**——pin 的意义就是不许静默漂移（D2），eval/重放的
          证据链靠它；
        - 备份=当前 serving 厂商时跳过（同一上游再打一遍不是备份）；
        - toolcall 请求且备份厂商不支持 tool calling → 跳过（planner 有 salvage/JSON
          自己的退路，别把请求改形状）。
        env 每请求现读（与 provider 热切同哲学）；未配置/拼错 fail-open 跳过。
        """
        if pinned:
            return None
        raw = (os.getenv("LLM_BACKUP") or "").strip()
        if not raw:
            return None
        pid_raw, _, model = raw.partition(":")
        entry = self.runtime.provider_entry(pid_raw.strip())
        if entry is None:
            logger.debug("LLM_BACKUP provider 未配置，忽略: %s", raw)
            return None
        pid, provider = entry
        if pid == aid:
            return None
        if tools_spec is not None:
            cap = getattr(self.runtime, "supports_toolcall", None)
            if cap is not None and not cap(pid):
                return None
        models = self.runtime.resolve_models_for(pid, model.strip())
        return (provider, pid, models[0]) if models else None

    @staticmethod
    def _msgs(request):
        msgs = [{"role": m.role, "content": m.content} for m in request.messages]
        # M4 P4 视觉：meta 只带 frame_id（16 字节），图像本体在网关内存里活最多两分钟。
        # 命中即把**最后一条 user 消息**升级为 OpenAI 多模态 content 数组。
        # 拿不到帧（过期/不存在）→ **显式失败**（见 FrameUnavailable），不静默退化成
        # 文本问答让模型编一个「那可能是一座写字楼」（铁律③）。
        fid = (dict(request.meta).get("vision_frame_id") or "").strip() if request.meta else ""
        if not fid:
            return msgs
        import vision_frames
        url = vision_frames.store().data_url(fid)
        if not url:
            # **声明了要看图却拿不到图 = 错误，不是「那就只发文字吧」**。
            # 静默降级会让 VL 模型对着空气答「看不清，画面有点模糊」——它在假装看到了
            # 一张模糊的图（真栈 e2e ⑤ 实测原话）。那比说不出更糟：用户没法判断真假。
            raise FrameUnavailable(fid)
        for m in reversed(msgs):
            if m["role"] == "user":
                m["content"] = [{"type": "text", "text": m["content"]},
                                {"type": "image_url", "image_url": {"url": url}}]
                break
        return msgs

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

    @staticmethod
    def _tools_spec(request) -> dict | None:
        """M1a：读 CompleteRequest.tools（Struct，线格式 {"tools":[...],"tool_choice":...}，
        RFC §3.1）。未设置/空/畸形 → None（纯文本路径，行为与今天逐字一致）。"""
        try:
            if not request.HasField("tools"):
                return None
            spec = MessageToDict(request.tools)
        except Exception:
            return None
        return spec if isinstance(spec, dict) and spec.get("tools") else None

    async def Complete(self, request, context):
        try:
            msgs = self._msgs(request)
        except FrameUnavailable as e:
            # 调用方（vision Agent）据此诚实说「画面已过期」，而不是让模型对着空气编。
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        temp = request.temperature or 0.7
        max_tokens = request.max_tokens or 512
        thinking = self._thinking(request)
        tools_spec = self._tools_spec(request)   # M1a：非 None 即 tool-calling 路径

        # 限流
        caller = dict(request.meta).get("caller", "default")
        if not self.limiter.allow(caller):
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "rate limited")

        # 请求级 pin 解析（D2）：pinned 请求按指定厂商的档位表走，未配置 fail-closed
        try:
            provider, aid, models, pinned = self._serving(request)
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))

        # M-D 能力协商：这一档不支持 tool calling 就**当场退回纯文本**，不要拿着 tools
        # 去打上游。此前没有能力位也没有熔断，不支持的 provider 每轮白打 2 次
        # （primary 一次 400、fast 再一次 400），然后 planner 还要再走一遍 JSON 路径。
        # 能力在**每次请求**读（provider 是可热切的），所以热切之后不会沿用旧能力。
        _cap = getattr(self.runtime, "supports_toolcall", None)
        if tools_spec and _cap is not None and not _cap(aid):
            logger.info("provider %s 不支持 tool calling，本次退回纯文本（不打上游）", aid)
            tools_spec = None

        # 缓存查找（serving provider + thinking 并入 key，避免切换/pin/开关思考结果串味）。
        # 带 tools 的请求跳过缓存：tools 不进缓存键会串味（同 messages 不同工具面），而
        # planner 上下文轮轮不同命中率≈0——跳过换正确性（RFC §8-4；键改造留 V2）。
        cached = None if tools_spec else self.cache.get(msgs, f"{aid}:{models[0]}", temp, thinking)
        if cached:
            content, used, finish, usage = cached
            logger.debug("Cache hit")
            await self._emit_llm(request, model=used, latency_ms=0.0, cache_hit=True,
                                 usage=usage, thinking=thinking, msgs=msgs,
                                 content=content, provider=aid, pinned=pinned)
            return llm_pb2.CompleteResponse(
                content=content, model_used=used, finish_reason=finish,
                prompt_tokens=usage[0], completion_tokens=usage[1])

        # 调用（带降级：同厂商 primary→fast，链尾追加跨厂商备份档 LLM_BACKUP）。
        # 429 单独分类（D3）：Retry-After 小且预算余量足 → 等一次重试同模型；否则
        # **跳过同厂商剩余档位**（限流是账号/厂商级，同厂白打）——但备份厂商照试
        # （换厂商正是对限流/上游抖动的对症解）。
        attempts = [(provider, aid, m) for m in models]
        backup = self._backup_serving(aid, pinned, tools_spec)
        if backup is not None:
            attempts.append(backup)
        last_err = None
        rate_limited_pids: set[str] = set()
        t_all = time.monotonic()
        for a_provider, a_aid, model in attempts:
            if a_aid in rate_limited_pids:
                continue
            if a_aid != aid:
                logger.warning("active 厂商 %s 整链失败，尝试备份档 %s:%s",
                               aid, a_aid, model)
            waited_429 = False
            while True:
                t0 = time.monotonic()
                try:
                    if tools_spec:
                        content, used, finish, usage, tool_calls = await a_provider.complete_tools(
                            msgs, model, temp, max_tokens,
                            tools=tools_spec.get("tools"),
                            tool_choice=tools_spec.get("tool_choice"),
                            thinking=thinking)
                    else:
                        content, used, finish, usage = await a_provider.complete(
                            msgs, model, temp, max_tokens, thinking=thinking)
                        tool_calls = []
                    latency_ms = (time.monotonic() - t0) * 1000

                    # 写缓存（tools 路径不写，与查同门控；键按**实际 serving 厂商**）
                    if not tools_spec:
                        self.cache.put(msgs, f"{a_aid}:{model}", temp, content, used, thinking)

                    # 记录成本 + 健康
                    cost_tracker.record(used, usage[0], usage[1], latency_ms)
                    health_tracker.record(a_aid, True, latency_ms=latency_ms)
                    # tool call 场景 content 常为空：obs content_head 以工具名单补记（RFC §5）
                    obs_content = content or (
                        "[tool_calls] " + ",".join(tc.get("name", "") for tc in tool_calls)
                        if tool_calls else "")
                    await self._emit_llm(request, model=used, latency_ms=latency_ms,
                                         usage=usage, thinking=thinking, msgs=msgs,
                                         content=obs_content, provider=a_aid, pinned=pinned)

                    out = llm_pb2.CompleteResponse(
                        content=content, model_used=used, finish_reason=finish,
                        prompt_tokens=usage[0], completion_tokens=usage[1])
                    if tool_calls:
                        # 回填 Struct（线格式 {"tool_calls":[{id,name,arguments}]}，RFC §3.1）
                        out.tool_calls.update({"tool_calls": tool_calls})
                    return out
                except Exception as e:
                    latency_ms = (time.monotonic() - t0) * 1000
                    cost_tracker.record(model, 0, 0, latency_ms, error=True)
                    last_err = e
                    if isinstance(e, ProviderHTTPError) and e.status_code == 429:
                        health_tracker.record(a_aid, False, kind="rate_limited", error=str(e))
                        ra = e.retry_after
                        remaining = context.time_remaining()
                        if (not waited_429 and ra is not None and ra <= _429_WAIT_CAP_S
                                and (remaining is None or remaining > ra + 2.0)):
                            waited_429 = True
                            logger.info("429 Retry-After=%.1fs，等待后重试 %s", ra, model)
                            await asyncio.sleep(ra)
                            continue          # 等一次重试同模型（仅一次）
                        rate_limited_pids.add(a_aid)   # 跳过**该厂商**剩余档位
                        break
                    health_tracker.record(
                        a_aid, False,
                        kind="timeout" if isinstance(e, httpx.TimeoutException) else "",
                        error=str(e))
                    logger.warning("Model %s failed: %s; trying next", model, e)
                    break
        # 终态按**最后一次**失败定性（备份也失败时以备份的错为准——它是最后的屏障）
        rate_limited = (isinstance(last_err, ProviderHTTPError)
                        and last_err.status_code == 429)

        # 错误映射：429→RESOURCE_EXHAUSTED（SDK 对它不做重连重试——那是连接语义，白打）；
        # 上游超时 → DEADLINE_EXCEEDED（非 UNAVAILABLE），避免调用方 SDK 把它当瞬时错误重试
        # 一次致延迟翻倍（曾因此 info/trip 接地合成爆 step 预算）。连接级失败仍 UNAVAILABLE 供重试。
        await self._emit_llm(
            request, model=models[0],
            latency_ms=(time.monotonic() - t_all) * 1000,
            status=("rate_limited" if rate_limited
                    else "timeout" if isinstance(last_err, httpx.TimeoutException) else "err"),
            error=str(last_err), thinking=thinking, msgs=msgs,
            provider=aid, pinned=pinned)
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
        try:
            msgs = self._msgs(request)
        except FrameUnavailable as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))
        thinking = self._thinking(request)
        try:
            provider_obj, aid, models, pinned = self._serving(request)   # 请求级 pin（D2）
        except ValueError as e:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except FrameUnavailable as e:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))

        # 流式不走缓存。**首 token 前**失败按档位链降级到下一模型（D4，兑现 R3.5 记录的
        # 「CompleteStream 无备用模型重试」缺口），链尾追加跨厂商备份档（LLM_BACKUP，
        # 与 Complete 同门控：pinned 恒不跨）；**首 token 后**不切——半段话术不可拼接，
        # 宁可 abort 让调用方走既有失败路径。
        attempts = [(provider_obj, aid, m) for m in models]
        backup = self._backup_serving(aid, pinned, None)
        if backup is not None:
            attempts.append(backup)
        last_err = None
        for a_provider, a_aid, model in attempts:
            if a_aid != aid:
                logger.warning("active 厂商 %s 流式整链失败，尝试备份档 %s:%s",
                               aid, a_aid, model)
            t0 = time.monotonic()
            head: list[str] = []
            head_len = 0
            first_token = False
            try:
                async for delta in a_provider.stream(
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
                health_tracker.record(a_aid, True, latency_ms=latency_ms)
                await self._emit_llm(request, model=model, latency_ms=latency_ms,
                                     thinking=thinking, msgs=msgs, content="".join(head),
                                     provider=a_aid, pinned=pinned)
                return
            except Exception as e:
                latency_ms = (time.monotonic() - t0) * 1000
                cost_tracker.record(model, 0, 0, latency_ms, error=True)
                kind = ("rate_limited"
                        if isinstance(e, ProviderHTTPError) and e.status_code == 429
                        else "timeout" if isinstance(e, httpx.TimeoutException) else "")
                health_tracker.record(a_aid, False, kind=kind, error=str(e))
                last_err = e
                if first_token:   # 已流出内容：不可换模型拼接，按既有语义直接失败
                    await self._emit_llm(request, model=model, latency_ms=latency_ms,
                                         status="err", error=str(e), thinking=thinking,
                                         msgs=msgs, provider=a_aid, pinned=pinned)
                    code = (grpc.StatusCode.DEADLINE_EXCEEDED
                            if isinstance(e, httpx.TimeoutException)
                            else grpc.StatusCode.UNAVAILABLE)
                    await context.abort(code, str(e))
                logger.warning("stream model %s failed before first token: %s; trying next",
                               model, e)

        await self._emit_llm(request, model=models[0], latency_ms=0.0,
                             status="err", error=str(last_err), thinking=thinking, msgs=msgs,
                             provider=aid, pinned=pinned)
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
