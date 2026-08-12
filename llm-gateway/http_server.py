"""HTTP 代理层：让 HMI 前端能调用 ASR/TTS 服务。

HMI 是浏览器环境，不能直接调 gRPC。此模块在 llm-gateway 同进程内启动一个
轻量 HTTP server，暴露 /api/asr 和 /api/tts 端点。

端口：LLM_GATEWAY_PORT + 1（默认 50053，但与 memory 冲突，用 50059）。
"""
from __future__ import annotations
import asyncio
import base64
import hmac
import json
import logging
import os

import aiohttp
import grpc
from aiohttp import web

from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from runtime.grpcio import aio_channel
from runtime.privacy_delete_bus import (
    CLOUD_SUBJECT,
    CLOUD_TARGET,
    MCP_SUBJECT,
    MCP_TARGET,
    PrivacyDeleteProtocolError,
    load_control_key,
    request_delete,
    validate_user_id,
)

from providers import (
    build_asr_provider, build_streaming_asr_provider, build_tts_provider,
    build_tts_stream_provider, emotion_instruct, TTS_STREAM_CATALOG,
)
from llm_runtime import get_runtime

logger = logging.getLogger("llm.http")

# 观测指标（best-effort，无 NATS/observability 不可导入时自动降级 log）。
try:
    from observability.events import get_emitter
    from observability.redact import gate_content as _gate_content
    from observability.tracing import set_session_id as _set_obs_session
    _obs = get_emitter("llm-gateway")
except Exception:  # observability 不在 path（如精简镜像）→ log-only
    _obs = None
    _gate_content = None
    _set_obs_session = None

# 跨域允许的方法。加新方法的端点时必须同步这里，否则浏览器 preflight 会挡在门外
# （见 create_http_app 的 cors_middleware；契约测试 test_http_cors.py 会自动比对）。
CORS_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"

_WAV_FORMATS = frozenset({"wav", "pcm", "pcm16"})
# R4.3b P2 B1：流式 ASR 的「前端直传 s16le PCM」格式（跳过 ffmpeg，支持前滚缓冲注入根治漏字 U4）
_PCM_STREAM_FORMATS = frozenset({"pcm16le", "s16le", "pcm", "pcm16"})


async def _transcode_to_wav(audio_bytes: bytes, src_format: str) -> bytes:
    """Transcode audio to 16kHz mono PCM16 WAV using ffmpeg.

    Passes through unchanged if *src_format* is already wav/pcm.
    Falls back to the original bytes if ffmpeg is unavailable or fails.
    """
    if src_format in _WAV_FORMATS:
        return audio_bytes

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", "pipe:0",
            "-ar", "16000", "-ac", "1", "-f", "wav",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate(input=audio_bytes)
        if proc.returncode == 0 and stdout:
            logger.info("ffmpeg transcode: %s -> wav (%d -> %d bytes)",
                        src_format, len(audio_bytes), len(stdout))
            return stdout
        logger.warning("ffmpeg exited %d, falling back to original", proc.returncode)
    except FileNotFoundError:
        logger.warning("ffmpeg not installed, skipping transcode for %s", src_format)

    return audio_bytes

MEMORY_ADDR = os.getenv("MEMORY_ADDR", "memory:50053")
_mem_channel = None
PRIVACY_DELETE_TIMEOUT_S = 3.0
PRIVACY_CONNECT_TIMEOUT_S = 2.0


def _auth_token_owners(raw: str, *, default_user_id: str) -> dict[str, str]:
    """Mirror edge-gateway AUTH_TOKENS parsing for destructive HTTP calls."""
    table: dict[str, str] = {}
    for entry in (raw or "").split(";"):
        parts = entry.strip().split(":", 3)
        if len(parts) != 4:
            continue
        token = parts[0].strip()
        owner = parts[1].strip() or default_user_id
        if token and owner:
            table[token] = owner
    return table


def _authenticated_owner(request: web.Request) -> str:
    """Resolve a Bearer token to its configured owner; never trust the body."""
    raw_header = request.headers.get("Authorization", "")
    scheme, separator, candidate = raw_header.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not candidate:
        return ""
    if candidate != candidate.strip() or " " in candidate:
        return ""
    table = _auth_token_owners(
        os.getenv("AUTH_TOKENS", ""),
        default_user_id=os.getenv("AUTH_DEFAULT_USER_ID", "u1").strip() or "u1",
    )
    candidate_bytes = candidate.encode("utf-8")
    for configured, owner in table.items():
        if hmac.compare_digest(candidate_bytes, configured.encode("utf-8")):
            return owner
    return ""


def _memory_stub():
    """memory gRPC 客户端（懒连接、复用）。HMI 是浏览器、不能直连 gRPC，
    经本 HTTP 代理读记忆内容（只读）。"""
    global _mem_channel
    if _mem_channel is None:
        _mem_channel = aio_channel(MEMORY_ADDR)
    return memory_pb2_grpc.MemoryStub(_mem_channel)


async def _delete_privacy_state(user_id: str) -> bool:
    """Delete MCP drafts and cloud pending sessions; any NACK is pending."""
    nc = None
    try:
        control_key = load_control_key()
        import nats
        nc = await nats.connect(
            os.getenv("NATS_URL", "nats://nats:4222"),
            connect_timeout=PRIVACY_CONNECT_TIMEOUT_S,
            max_reconnect_attempts=0,
        )

        async def one(subject: str, target: str) -> bool:
            try:
                return await request_delete(
                    nc,
                    subject=subject,
                    target=target,
                    user_id=user_id,
                    timeout=PRIVACY_DELETE_TIMEOUT_S,
                    key=control_key,
                )
            except Exception:
                return False

        results = await asyncio.gather(
            one(MCP_SUBJECT, MCP_TARGET),
            one(CLOUD_SUBJECT, CLOUD_TARGET),
        )
        return all(result is True for result in results)
    except Exception:
        return False
    finally:
        if nc is not None:
            try:
                await nc.close()
            except Exception:
                pass

# 从环境变量读音色配置
DEFAULT_VOICE = os.getenv("TTS_VOICE_ID", "冰糖")
DEFAULT_TTS_MODEL = os.getenv("TTS_MODEL", "mimo-v2.5-tts")
DEFAULT_ASR_MODEL = os.getenv("ASR_MODEL", "mimo-v2.5-asr")


def create_http_app() -> web.Application:
    asr = build_asr_provider()
    tts = build_tts_provider()

    routes = web.RouteTableDef()

    @routes.post("/api/asr")
    async def handle_asr(request: web.Request):
        """ASR：接收音频 base64，返回识别文本。
        请求体：{"audio": "base64...", "format": "wav", "language": "zh"}
        响应：{"text": "...", "confidence": 0.9, "duration_ms": 1234}
        """
        try:
            raw = await request.read()
            body = json.loads(raw)
            audio_b64 = body.get("audio", "")
            if not audio_b64:
                return web.json_response({"error": "missing audio"}, status=400)

            audio_bytes = base64.b64decode(audio_b64)
            fmt = body.get("format", "wav")
            lang = body.get("language", "zh")

            # Transcode webm/ogg/etc to wav so the ASR backend always gets
            # a compatible format (browser MediaRecorder produces webm/opus).
            audio_bytes = await _transcode_to_wav(audio_bytes, fmt)
            fmt = "wav"

            text, conf, lang_out, model, dur = await asr.transcribe(
                audio=audio_bytes, fmt=fmt, language=lang, model=DEFAULT_ASR_MODEL)

            logger.info("ASR: %d bytes -> %d chars", len(audio_bytes), len(text))
            return web.json_response({
                "text": text, "confidence": conf, "language": lang_out,
                "duration_ms": dur, "model": model,
            })
        except Exception as e:
            logger.warning("ASR error: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    @routes.post("/api/tts")
    async def handle_tts(request: web.Request):
        """TTS：接收文本，返回音频 base64。
        请求体：{"text": "...", "voice_id": "冰糖", "format": "wav"}
        响应：{"audio": "base64...", "format": "wav", "duration_ms": 1234}
        """
        try:
            raw = await request.read()
            body = json.loads(raw)
            # 批处理 TTS 与流式漏斗（providers._sentence_segments）同口径：合成前剥 markdown，
            # 星号/井号/表格符不进语音（覆盖卡片全文朗读等 HMI 直发路径）。
            from providers import _strip_md_tts
            text = _strip_md_tts(body.get("text", ""))
            if not text:
                return web.json_response({"error": "missing text"}, status=400)

            voice_id = body.get("voice_id", DEFAULT_VOICE)
            fmt = body.get("format", "wav")
            provider_pin = body.get("provider", "")
            if not isinstance(provider_pin, str):
                return web.json_response(
                    {"error": "provider must be a string"},
                    status=400,
                )
            provider_pin = provider_pin.strip().lower()
            supported_batch_providers = (
                set(TTS_STREAM_CATALOG) | {"mimo", "xiaomimimo"}
            )
            if provider_pin and provider_pin not in supported_batch_providers:
                return web.json_response(
                    {"error": "unsupported TTS provider"},
                    status=400,
                )
            request_tts = (
                build_tts_provider(provider_pin)
                if provider_pin
                else tts
            )

            audio_bytes, fmt_out, dur, model, voice = await request_tts.synthesize(
                text=text, voice_id=voice_id, model=DEFAULT_TTS_MODEL,
                speed=1.0, fmt=fmt)

            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
            logger.info("TTS: %d chars -> %d bytes, voice=%s", len(text), len(audio_bytes), voice)
            return web.json_response({
                "audio": audio_b64, "format": fmt_out, "duration_ms": dur,
                "provider": request_tts.provider,
                "model": model,
                "voice_id": voice,
            })
        except Exception as e:
            import traceback
            logger.warning("TTS error: %s\n%s", e, traceback.format_exc())
            return web.json_response({"error": str(e)}, status=500)

    @routes.get("/api/voices")
    async def handle_voices(request: web.Request):
        """查询音色列表。?provider=cosyvoice|qwen|mimo|minimax 查指定引擎；
        缺省=当前批处理 TTS 引擎自己的音色表（默认配置下即 MiMo，向后兼容）。"""
        provider = request.query.get("provider", "").strip().lower()
        if provider in TTS_STREAM_CATALOG:
            return web.json_response({"voices": list(TTS_STREAM_CATALOG[provider]["voices"])})
        lang = request.query.get("language", "")
        gender = request.query.get("gender", "")
        voices = await tts.list_voices(language=lang, gender=gender)
        return web.json_response({"voices": voices})

    @routes.get("/api/asr/stream/info")
    async def handle_asr_stream_info(request: web.Request):
        """流式 ASR 能力探测（HMI 设置页据此渲染引擎/模型选择 + 可用性）。"""
        has_dashscope = bool(os.getenv("DASHSCOPE_ASR_KEY") or os.getenv("LLM_EMBED_API_KEY"))
        has_mimo = bool(os.getenv("LLM_API_KEY"))
        return web.json_response({
            "streaming": has_dashscope or has_mimo,
            "default": os.getenv("ASR_STREAM_PROVIDER", "dashscope"),
            "providers": [
                {"id": "dashscope", "label": "DashScope 实时", "available": has_dashscope,
                 "models": ["Qwen3-ASR-Flash-Realtime-2026-02-10", "fun-asr-realtime"]},
                {"id": "mimo", "label": "MiMo 分块", "available": has_mimo,
                 "models": ["mimo-v2.5-asr"]},
            ],
        })

    @routes.get("/api/asr/stream")
    async def handle_asr_stream(request: web.Request):
        """流式 ASR：HMI 经 WebSocket 推音频帧（webm/opus）+ start/stop 控制，
        网关流式 ffmpeg 转 PCM16→流式引擎（DashScope 实时 / MiMo 分块）→回 partial/final。
        见 docs/design/2026-06-30-asr-streaming-design.md。批处理 /api/asr 不受影响（回退路径）。"""
        import time
        import uuid as _uuid

        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        ffmpeg = None
        tasks: list = []
        pcm_queue: asyncio.Queue = asyncio.Queue()
        pcm_direct = False  # True=前端直传 s16le PCM（跳 ffmpeg，B1）
        started = False     # 已收到 start（防重复；PCM 模式无 ffmpeg 句柄可判）
        asr_meta = {"provider": "", "model": ""}  # 观测：本条 utterance 的引擎归属

        async def pcm_iter():
            while True:
                chunk = await pcm_queue.get()
                if chunk is None:
                    return
                yield chunk

        async def read_ffmpeg(proc):
            try:
                while True:
                    data = await proc.stdout.read(3200)
                    if not data:
                        break
                    await pcm_queue.put(data)
            finally:
                await pcm_queue.put(None)

        async def run_provider(provider, language):
            t0 = time.monotonic()
            final_text = ""
            status = "ok"
            try:
                async for r in provider.stream(pcm_iter(), language=language):
                    if ws.closed:
                        break
                    if r.get("final") and r.get("text"):
                        final_text = r["text"]
                    await ws.send_json({"type": "final" if r.get("final") else "partial",
                                        "text": r.get("text", "")})
                if not ws.closed:
                    await ws.send_json({"type": "done"})
            except Exception as e:
                status = "err"
                logger.warning("ASR stream provider error: %s", e)
                if not ws.closed:
                    await ws.send_json({"type": "error", "message": str(e)})
            finally:
                # 观测（badcase「听错了」排查）：每条 utterance 一个 asr.stream span，
                # 引擎/时延/定稿长度 + 门控定稿文本；session_id 经 contextvar 自动携带。
                if _obs is not None:
                    try:
                        await _obs.emit_span(
                            _uuid.uuid4().hex[:16], "asr.stream", status=status,
                            duration_ms=(time.monotonic() - t0) * 1000,
                            attrs={"provider": asr_meta["provider"],
                                   "model": asr_meta["model"],
                                   "chars": len(final_text),
                                   "text": (_gate_content(final_text, 120)
                                            if _gate_content else "")})
                    except Exception:
                        pass

        def _close_stdin():
            if ffmpeg and ffmpeg.stdin and not ffmpeg.stdin.is_closing():
                try:
                    ffmpeg.stdin.close()
                except Exception:
                    pass

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    mtype = data.get("type")
                    if mtype == "start":
                        if started:
                            continue
                        provider = build_streaming_asr_provider(
                            data.get("provider", ""), data.get("model", ""),
                            vad_silence_ms=int(data.get("vad_silence_ms") or 0))  # B2 静音尾透传
                        if provider is None:
                            await ws.send_json({"type": "unsupported"})
                            continue
                        started = True
                        # 观测：引擎归属 + HMI 会话（contextvar 随 create_task 复制传播）
                        asr_meta["provider"] = data.get("provider", "") or "default"
                        asr_meta["model"] = data.get("model", "")
                        if _set_obs_session and data.get("session_id"):
                            _set_obs_session(str(data["session_id"]))
                        fmt = (data.get("format", "") or "").lower()
                        if fmt in _PCM_STREAM_FORMATS:
                            # B1 PCM 直传：前端已转 16k mono s16le，BINARY 帧直入队列，跳 ffmpeg
                            # （支持前滚缓冲注入根治 U4 漏字；webm 路径逐字不动）
                            pcm_direct = True
                            tasks.append(asyncio.create_task(
                                run_provider(provider, data.get("language", "zh"))))
                        else:
                            ffmpeg = await asyncio.create_subprocess_exec(
                                "ffmpeg", "-i", "pipe:0", "-ar", "16000", "-ac", "1",
                                "-f", "s16le", "pipe:1",
                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL)
                            tasks.append(asyncio.create_task(read_ffmpeg(ffmpeg)))
                            tasks.append(asyncio.create_task(
                                run_provider(provider, data.get("language", "zh"))))
                    elif mtype == "stop":
                        if pcm_direct:
                            await pcm_queue.put(None)  # 流末 → pcm_iter 收尾 → 引擎定稿
                        else:
                            _close_stdin()  # flush ffmpeg → 流末 → 引擎定稿
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    if pcm_direct:
                        await pcm_queue.put(bytes(msg.data))  # 已是 s16le，直接入队
                    elif ffmpeg and ffmpeg.stdin and not ffmpeg.stdin.is_closing():
                        try:
                            ffmpeg.stdin.write(msg.data)
                            await ffmpeg.stdin.drain()
                        except Exception:
                            pass
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE,
                                  aiohttp.WSMsgType.CLOSING):
                    break
        finally:
            if pcm_direct:
                pcm_queue.put_nowait(None)  # 确保 pcm_iter 退出（重复 None 无害）
            _close_stdin()
            if ffmpeg:
                try:
                    ffmpeg.kill()
                except Exception:
                    pass
            for t in tasks:
                t.cancel()
        return ws

    @routes.get("/api/tts/stream/info")
    async def handle_tts_stream_info(request: web.Request):
        """流式 TTS 能力探测（HMI 设置页据此渲染引擎/音色两级选择 + 可用性 + 回退判定）。"""
        has_dashscope = bool(os.getenv("DASHSCOPE_ASR_KEY") or os.getenv("LLM_EMBED_API_KEY"))
        avail = {
            "cosyvoice": has_dashscope, "qwen": has_dashscope,
            "mimo": bool(os.getenv("LLM_API_KEY")),        # MiMo v2.5 已支持流式
            "minimax": bool(os.getenv("MINIMAX_API_KEY")),  # MiniMax T2A 流式
        }
        default = os.getenv("TTS_STREAM_PROVIDER", "cosyvoice").strip().lower()
        if default in ("", "off", "none"):
            default = "mimo"
        providers = []
        for pid in ("cosyvoice", "qwen", "mimo", "minimax"):
            cat = TTS_STREAM_CATALOG[pid]
            providers.append({
                "id": pid, "label": cat["label"], "streaming": True,
                "available": avail.get(pid, False), "model": cat["model"],
                "sample_rate": cat["sample_rate"], "voices": cat["voices"],
            })
        return web.json_response({
            "streaming": any(avail.values()), "default": default, "providers": providers,
        })

    @routes.get("/api/tts/stream")
    async def handle_tts_stream(request: web.Request):
        """流式 TTS：HMI 经 WebSocket 送文本增量 + 控制帧，网关经流式引擎（cosyvoice run-task /
        qwen realtime）边收边回 PCM 音频分片。见 docs/design/2026-07-04-r4.2-streaming-tts-bargein.md。
        批处理 /api/tts 不受影响（回退路径）。
        上行：{type:start, provider, model, voice, sample_rate?} / {type:text, delta} / {type:finish} / {type:cancel}
        下行：{type:meta, sample_rate, format}（首帧前）+ 二进制音频帧 + {type:done, chunks, first_chunk_ms} /
              {type:unsupported} / {type:error, message}"""
        import time
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        text_queue: asyncio.Queue = asyncio.Queue()
        tasks: list = []
        started = False
        cancelled = {"v": False}

        async def text_iter():
            while True:
                item = await text_queue.get()
                if item is None:
                    return
                yield item

        async def run_provider(provider, voice, sample_rate, instruct=""):
            t0 = time.monotonic()
            first_ms = None
            chunks = 0
            err = None
            try:
                async for out in provider.stream(text_iter(), voice=voice,
                                                 sample_rate=sample_rate,
                                                 instruct=instruct):
                    if ws.closed:
                        break
                    if isinstance(out, (bytes, bytearray)):
                        if out:
                            if first_ms is None:
                                first_ms = round((time.monotonic() - t0) * 1000)
                            chunks += 1
                            await ws.send_bytes(bytes(out))
                    elif isinstance(out, dict):
                        await ws.send_json(out)  # meta
                if not ws.closed:
                    await ws.send_json({"type": "done", "chunks": chunks, "first_chunk_ms": first_ms})
            except asyncio.CancelledError:
                cancelled["v"] = True
                raise
            except Exception as e:
                err = str(e)
                logger.warning("TTS stream provider error: %s", e)
                if not ws.closed:
                    await ws.send_json({"type": "error", "message": err})
            finally:
                total_ms = round((time.monotonic() - t0) * 1000)
                if _obs is not None:
                    try:
                        await _obs.emit_metric(
                            "tts-stream", count=1, avg_ms=total_ms,
                            error_rate=1.0 if err else 0.0,
                            tts_first_chunk_ms=first_ms or 0, tts_total_ms=total_ms,
                            tts_chunks=chunks, tts_cancelled=cancelled["v"])
                    except Exception:
                        pass
                logger.info("TTS stream done: first=%sms total=%dms chunks=%d cancelled=%s err=%s",
                            first_ms, total_ms, chunks, cancelled["v"], err)

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    mtype = data.get("type")
                    if mtype == "start":
                        if started:
                            continue
                        provider = build_tts_stream_provider(
                            data.get("provider", ""), data.get("model", ""), data.get("voice", ""))
                        if provider is None:
                            await ws.send_json({"type": "unsupported"})
                            continue
                        started = True
                        # M2 P2：会话级情绪 → TTS 指令化措辞（措辞表在 providers 侧；
                        # 引擎不支持 instruct 时形参被忽略，零行为变化）
                        tasks.append(asyncio.create_task(run_provider(
                            provider, data.get("voice", ""),
                            int(data.get("sample_rate") or 0),
                            emotion_instruct(data.get("emotion", "")))))
                    elif mtype == "text":
                        if started:
                            await text_queue.put(data.get("delta", ""))
                    elif mtype == "finish":
                        await text_queue.put(None)
                    elif mtype == "cancel":
                        cancelled["v"] = True
                        await text_queue.put(None)
                        break  # barge-in：断开 → 上游任务经 finally 取消，不再吐帧
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE,
                                  aiohttp.WSMsgType.CLOSING):
                    break
        finally:
            text_queue.put_nowait(None)  # 确保 text_iter 退出
            for t in tasks:
                t.cancel()
        return ws

    @routes.get("/api/memory/session")
    async def handle_mem_session(request: web.Request):
        """读会话对话记忆（HMI 记忆视图）。?session_id=&last_n=20"""
        sid = request.query.get("session_id", "")
        last_n = int(request.query.get("last_n", "20") or 20)
        uid = (request.query.get("user_id") or "").strip()
        occ = (request.query.get("occupant_id") or "").strip()
        # scope=all 是**显式**管理视图（设置页「全部乘员会话」），缺省一律 OWNER_ONLY。
        all_occ = (request.query.get("scope") or "").strip().lower() == "all"
        if not sid:
            return web.json_response({"turns": []})
        try:
            resp = await _memory_stub().GetSession(
                memory_pb2.GetSessionRequest(
                    session_id=sid, last_n=last_n, user_id=uid,
                    occupant_id=occ or "primary",
                    scope=(memory_pb2.HISTORY_SCOPE_ALL_OCCUPANTS if all_occ
                           else memory_pb2.HISTORY_SCOPE_OWNER_ONLY)), timeout=5)
            turns = [{"role": t.role, "text": t.text, "ts": t.ts,
                      "occupant_id": t.occupant_id} for t in resp.turns]
            return web.json_response({"turns": turns})
        except Exception as e:
            logger.warning("memory session read error: %s", e)
            return web.json_response({"turns": [], "error": str(e)})

    @routes.get("/api/memory/context")
    async def handle_mem_context(request: web.Request):
        """读上下文/画像（偏好、车辆状态等）。?session_id=&user_id=&vehicle_id=&scopes=a,b"""
        sid = request.query.get("session_id", "")
        uid = request.query.get("user_id", "")
        vid = request.query.get("vehicle_id", "")
        scopes = [s for s in request.query.get("scopes", "").split(",") if s] or \
            ["profile.taste", "vehicle.state", "vehicle.location"]
        try:
            resp = await _memory_stub().GetContext(memory_pb2.GetContextRequest(
                session_id=sid, user_id=uid, vehicle_id=vid, scopes=scopes,
                occupant_id=(request.query.get("occupant_id") or "").strip() or "primary"),
                timeout=5)
            return web.json_response({"values": dict(resp.values)})
        except Exception as e:
            logger.warning("memory context read error: %s", e)
            return web.json_response({"values": {}, "error": str(e)})

    @routes.get("/api/memory/profile")
    async def handle_mem_profile(request: web.Request):
        """读用户**真实学到的**记忆（HMI 记忆视图）：偏好/常去地点/情景。
        走分层记忆 ExportUser（非 mock context），只取现行（未被取代）。?user_id="""
        uid = request.query.get("user_id", "")
        # 缺省只看当前乘员（M-B）：面板此前把全部乘员的记忆混在一起列，
        # 而「删除」按钮又是按 scope 删的——看到的是别人的，删掉的是所有人的。
        occ = (request.query.get("occupant_id") or "").strip() or "primary"
        all_occ = (request.query.get("scope") or "").strip().lower() == "all"
        empty = {"preferences": [], "places": [], "episodes": []}
        if not uid:
            return web.json_response(empty)
        try:
            resp = await _memory_stub().ExportUser(
                memory_pb2.ExportUserRequest(user_id=uid), timeout=5)
            data = json.loads(resp.json) if resp.json else {}
        except Exception as e:
            logger.warning("memory profile read error: %s", e)
            return web.json_response({**empty, "error": str(e)})
        prefs, places, episodes = [], [], []
        for m in data.get("memories", []):
            if m.get("superseded_by"):
                continue  # 只展示现行
            if not all_occ and (m.get("occupant_id") or "primary") != occ:
                continue
            pred = m.get("predicate") or ""
            if m.get("kind") == "episodic":
                episodes.append({"text": m.get("text", ""),
                                 "ts": m.get("source_ts") or m.get("created_at") or 0})
            elif pred.startswith("place."):
                try:
                    v = json.loads(m.get("value_json") or "{}")
                except (json.JSONDecodeError, TypeError):
                    v = {}
                places.append({"key": pred.split(".", 1)[1],
                               "id": m.get("id", ""),
                               "occupant_id": m.get("occupant_id") or "primary",
                               "name": v.get("name") or v.get("address") or m.get("text", ""),
                               "address": v.get("address", ""), "scope": m.get("scope", "")})
            elif m.get("kind") == "semantic":
                # id/owner/predicate 一并回传：L1 精确删除按 item id 走，
                # 面板不再需要（也不允许）用 scope 去删「这一行」。
                prefs.append({"id": m.get("id", ""), "predicate": pred,
                              "occupant_id": m.get("occupant_id") or "primary",
                              "managed": pred in ("identity.name",),
                              "text": m.get("text", ""),
                              "scope": m.get("scope", ""),
                              "provenance": m.get("provenance", ""),
                              "confidence": m.get("confidence", 0)})
        return web.json_response({"preferences": prefs, "places": places, "episodes": episodes})

    @routes.delete("/api/memory/items/{item_id}")
    async def handle_mem_delete_item(request: web.Request):
        """L1：删一条记忆。query: user_id, occupant_id。

        取代「单行删除走 `POST /api/memory/forget` + scope」——那是按 scope 删，
        会连带清掉该 scope 下所有乘员的条目（删自己的名字＝删光全车的名字）。
        occupant 必填：owner 级动作绝不从空值推断范围。
        """
        uid = (request.query.get("user_id") or "").strip()
        occ = (request.query.get("occupant_id") or "").strip()
        item_id = request.match_info.get("item_id", "").strip()
        if not uid or not occ or not item_id:
            return web.json_response({"ok": False, "error": "missing_owner"}, status=400)
        try:
            resp = await _memory_stub().DeleteMemoryItem(
                memory_pb2.DeleteMemoryItemRequest(
                    user_id=uid, occupant_id=occ, item_id=item_id), timeout=5)
        except Exception as e:
            logger.warning("memory item delete error: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        status = 200 if resp.ok else {"missing_owner": 400, "not_found": 404,
                                      "managed_memory": 409}.get(resp.error, 500)
        return web.json_response({"ok": resp.ok, "error": resp.error,
                                  "deleted": resp.deleted,
                                  "deleted_relations": resp.deleted_relations},
                                 status=status)

    @routes.post("/api/memory/forget")
    async def handle_mem_forget(request: web.Request):
        """删除用户记忆（HMI 管理）。body: {user_id, scope?}。scope 空=清空全部（GDPR 硬删）。"""
        authenticated_owner = _authenticated_owner(request)
        if not authenticated_owner:
            return web.json_response({
                "ok": False,
                "error": "unauthorized",
            }, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({
                "ok": False,
                "error": "invalid_request",
            }, status=400)
        if not isinstance(body, dict):
            return web.json_response({
                "ok": False,
                "error": "invalid_request",
            }, status=400)
        raw_uid = body.get("user_id")
        if "scope" in body and not isinstance(body["scope"], str):
            return web.json_response({
                "ok": False,
                "error": "invalid_scope",
            }, status=400)
        raw_scope = body.get("scope", "")
        uid = raw_uid.strip() if isinstance(raw_uid, str) else ""
        scope = raw_scope.strip()
        if not uid:
            return web.json_response({"ok": False}, status=400)
        if not hmac.compare_digest(
            uid.encode("utf-8"), authenticated_owner.encode("utf-8"),
        ):
            return web.json_response({
                "ok": False,
                "error": "owner_mismatch",
            }, status=403)
        try:
            # Validate before Memory ForgetUser so malformed authenticated
            # owners cannot produce partial or owner-ambiguous deletion.
            validate_user_id(uid)
        except PrivacyDeleteProtocolError:
            return web.json_response({
                "ok": False,
                "error": "invalid_user_id",
            }, status=400)
        try:
            resp = await _memory_stub().ForgetUser(memory_pb2.ForgetUserRequest(
                user_id=uid, scopes=[scope] if scope else []), timeout=5)
            if scope:
                return web.json_response({"ok": resp.ok, "deleted": resp.deleted})
            if not resp.ok:
                return web.json_response({
                    "ok": False,
                    "deleted": resp.deleted,
                    "pending": True,
                    "retryable": True,
                    "error": "privacy_delete_pending",
                }, status=503)
            if not await _delete_privacy_state(uid):
                return web.json_response({
                    "ok": False,
                    "deleted": resp.deleted,
                    "pending": True,
                    "retryable": True,
                    "error": "privacy_delete_pending",
                }, status=503)
            return web.json_response({
                "ok": True,
                "deleted": resp.deleted,
                "pending": False,
            })
        except Exception as e:
            logger.warning("memory forget error: %s", type(e).__name__)
            return web.json_response({
                "ok": False,
                "pending": True,
                "retryable": True,
                "error": "privacy_delete_pending",
            }, status=503)

    # ── 声纹（M4 P4）──────────────────────────────────────────────────────
    # 刻意**不旁路** /api/asr/stream 与 /api/s2s 的上行 PCM，而是独立端点：那两条是刚验完的
    # 关键路径（S2S 上批次才踩出端点死锁），为一个可选增强件去动它们，风险与收益不成比例；
    # 且独立端点一处实现同时覆盖 classic 与 s2s 两个挡位。代价是唤醒首句 ≤96KB 重复上行。
    # 详见 RFC §2.2。
    def _vp_provider():
        import speaker_embed as vp_mod
        return vp_mod, vp_mod.resolve_provider()

    @routes.get("/api/voiceprint/info")
    async def handle_vp_info(request: web.Request):
        """能力探测 + 乘员清单（HMI 设置页「乘员与声纹」据此渲染）。"""
        vp_mod, prov = _vp_provider()
        if prov is None:
            return web.json_response({"enabled": False, "provider": "disabled",
                                      "reason": vp_mod.disabled_reason(),
                                      "occupants": []})
        uid = (request.query.get("user_id") or "").strip()
        occupants, threshold, margin_ = [], 0.0, 0.0
        if uid:
            try:
                resp = await _memory_stub().ListVoiceprints(
                    memory_pb2.ListVoiceprintsRequest(user_id=uid, model=prov.model),
                    timeout=5)
                occupants = [{"occupant_id": o.occupant_id, "display_name": o.display_name,
                              "sample_count": o.sample_count, "stale": o.stale,
                              "self_consistency": round(o.self_consistency, 3),
                              "updated_at": o.updated_at} for o in resp.occupants]
                threshold, margin_ = resp.threshold, resp.margin
            except Exception as e:
                logger.warning("voiceprint list error: %s", e)
        return web.json_response({
            "enabled": True, "provider": prov.name, "model": prov.model, "dim": prov.dim,
            "occupants": occupants, "threshold": threshold, "margin": margin_,
            "min_speech_ms": vp_mod.min_speech_ms(),
        })

    @routes.post("/api/voiceprint/identify")
    async def handle_vp_identify(request: web.Request):
        """首句 PCM → occupant_id。query: user_id。body: 二进制 PCM（16k mono s16le）。

        **认不出一律回 primary**（诚实降级）——判定见 memory/voiceprint.py::decide。
        HMI 在唤醒后累计 1.5s 有效语音时就调本端点（用户还在说），故本路径要短平快。
        """
        vp_mod, prov = _vp_provider()
        if prov is None:
            return web.json_response({"occupant_id": "primary", "decision": "disabled"})
        uid = (request.query.get("user_id") or "").strip()
        pcm = await request.read()
        # 主链路（HMI 语音回路）直传 16k PCM 帧，不转码——它在唤醒窗内要短平快。
        # 设置页「试一试」用 MediaRecorder 录的是 webm，经既有 ffmpeg 转一次（低频操作）。
        src_fmt = (request.query.get("format") or "pcm16le").strip().lower()
        if src_fmt not in _PCM_STREAM_FORMATS:
            wav = await _transcode_to_wav(pcm, src_fmt)
            pcm = wav[44:] if wav[:4] == b"RIFF" else wav
        dur = vp_mod.pcm_duration_ms(pcm)
        if dur < vp_mod.min_speech_ms():
            return web.json_response({"occupant_id": "primary", "decision": "too_short",
                                      "duration_ms": dur})
        if not uid:
            return web.json_response({"occupant_id": "primary", "decision": "no_user"})
        try:
            vec = await asyncio.get_running_loop().run_in_executor(None, prov.embed, pcm)
            resp = await _memory_stub().IdentifySpeaker(memory_pb2.IdentifySpeakerRequest(
                user_id=uid, probe=memory_pb2.VoiceVector(values=vec),
                model=prov.model), timeout=5)
        except Exception as e:
            # 声纹是可选增强：任何失败都退回 primary，绝不让它把一轮对话拖死。
            logger.warning("voiceprint identify error: %s", e)
            return web.json_response({"occupant_id": "primary", "decision": "error"})
        out = {"occupant_id": resp.occupant_id, "display_name": resp.display_name,
               "decision": resp.decision, "score": round(resp.score, 4),
               "runner_up": round(resp.runner_up, 4), "duration_ms": dur}
        # **判定必须留下痕迹**：「认不出→回 primary」是静默降级，用户侧只表现为「换了个人
        # 还是同一个人」，服务端此前一行日志都没有。obs 那条 metric 也指望不上——collector
        # 的 apply_metric 是固定键白名单，vp_* 全被丢掉（2026-07-26 排查时发现）。
        # 阈值本来就是拿合成音色标定的、对真人多半要重调，没有分数就无从调起。
        logger.info("voiceprint identify: user=%s occupant=%s decision=%s "
                    "score=%.4f runner_up=%.4f probe_ms=%d src=%s",
                    uid, resp.occupant_id, resp.decision, resp.score, resp.runner_up,
                    dur, src_fmt)
        if _obs is not None:
            # 四态全进 obs：阈值不靠拍脑袋，靠线上分布（供 M1b nightly 挖掘调优）。
            # 走 metric 而非 span——识别发生在**本轮 trace 建立之前**（HMI 在用户还在说的时候
            # 就发了），没有 trace_id 可挂；硬造一个反而会在轮次详情里变成孤儿 span。
            try:
                await _obs.emit_metric(
                    "voiceprint", count=1, avg_ms=dur,
                    error_rate=0.0 if resp.decision == "accept" else 1.0,
                    vp_decision=resp.decision, vp_score=round(resp.score, 4),
                    vp_runner_up=round(resp.runner_up, 4),
                    vp_occupant=resp.occupant_id)
            except Exception:
                pass
        return web.json_response(out)

    @routes.post("/api/voiceprint/enroll")
    async def handle_vp_enroll(request: web.Request):
        """注册/更新一个乘员的声纹。query: user_id, display_name, occupant_id?。
        body: multipart 的多段 PCM（字段名 sample），或单段二进制（自洽度=1）。

        **第一个注册的人由 memory 侧分配到 primary**（RFC §3.4）——不在这里判，
        避免两处各有一套「谁是第一个」的逻辑。
        """
        vp_mod, prov = _vp_provider()
        if prov is None:
            return web.json_response({"ok": False, "error": "disabled"}, status=503)
        uid = (request.query.get("user_id") or "").strip()
        if not uid:
            return web.json_response({"ok": False, "error": "missing user_id"}, status=400)
        # 注册走 multipart（每段一个 sample 字段）。format=webm 时经既有 ffmpeg 转码——
        # 注册是低频操作，让 HMI 复用已验证的 MediaRecorder 路径比新写一套 PCM 采集稳。
        # 识别路径**不转码**（它在唤醒窗内、要短平快，HMI 那边本来就有 16k PCM 帧）。
        src_fmt = (request.query.get("format") or "pcm16le").strip().lower()
        segments: list[bytes] = []
        if request.content_type and "multipart" in request.content_type:
            reader = await request.multipart()
            async for part in reader:
                if part.name == "sample":
                    segments.append(await part.read(decode=False))
        else:
            raw = await request.read()
            if raw:
                segments.append(raw)
        if src_fmt not in _PCM_STREAM_FORMATS:
            decoded = []
            for s in segments:
                wav = await _transcode_to_wav(s, src_fmt)
                decoded.append(wav[44:] if wav[:4] == b"RIFF" else wav)   # 去 WAV 头留裸 PCM
            segments = decoded
        segments = [s for s in segments if vp_mod.pcm_duration_ms(s) >= vp_mod.min_speech_ms()]
        if not segments:
            return web.json_response({"ok": False, "error": "no_valid_samples"}, status=400)
        try:
            loop = asyncio.get_running_loop()
            vecs = [await loop.run_in_executor(None, prov.embed, s) for s in segments]
            resp = await _memory_stub().EnrollVoiceprint(memory_pb2.EnrollVoiceprintRequest(
                user_id=uid, occupant_id=(request.query.get("occupant_id") or "").strip(),
                display_name=(request.query.get("display_name") or "").strip(),
                samples=[memory_pb2.VoiceVector(values=v) for v in vecs],
                model=prov.model), timeout=10)
        except Exception as e:
            logger.warning("voiceprint enroll error: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        if not resp.ok:
            # low_consistency：三段互不像 → 拒绝建模板（建了此后谁都认不准）。
            return web.json_response(
                {"ok": False, "error": resp.error,
                 "self_consistency": round(resp.self_consistency, 3)}, status=409)
        return web.json_response({"ok": True, "occupant_id": resp.occupant_id,
                                  "display_name": resp.display_name,
                                  "sample_count": resp.sample_count,
                                  "self_consistency": round(resp.self_consistency, 3)})

    @routes.patch("/api/voiceprint/{occupant_id}")
    async def handle_vp_rename(request: web.Request):
        """改称呼。query: user_id, display_name。**不重录三段**——名字是元数据。"""
        uid = (request.query.get("user_id") or "").strip()
        occ = request.match_info.get("occupant_id", "").strip()
        name = (request.query.get("display_name") or "").strip()
        if not uid or not occ or not name:
            return web.json_response({"ok": False, "error": "empty_name"}, status=400)
        try:
            resp = await _memory_stub().RenameVoiceprint(memory_pb2.RenameVoiceprintRequest(
                user_id=uid, occupant_id=occ, display_name=name), timeout=5)
        except Exception as e:
            logger.warning("voiceprint rename error: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        # 失败码要能区分「没这个人」和「这个名字被占了」（M-B）：都回 404 时，
        # 前端只能说「改名失败」，用户不知道换个称呼就能成。
        fail_status = {"duplicate_name": 409, "empty_name": 400}.get(resp.error, 404)
        return web.json_response({"ok": resp.ok, "display_name": resp.display_name,
                                  "error": resp.error},
                                 status=200 if resp.ok else fail_status)

    @routes.delete("/api/voiceprint/{occupant_id}")
    async def handle_vp_delete(request: web.Request):
        """删除一个乘员。query: user_id, purge_memory=1|0（默认 1=连同其记忆一起忘掉）。"""
        uid = (request.query.get("user_id") or "").strip()
        occ = request.match_info.get("occupant_id", "").strip()
        if not uid or not occ:
            return web.json_response({"ok": False}, status=400)
        purge = (request.query.get("purge_memory") or "1").strip() not in ("0", "false")
        try:
            resp = await _memory_stub().DeleteVoiceprint(memory_pb2.DeleteVoiceprintRequest(
                user_id=uid, occupant_id=occ, purge_memory=purge), timeout=10)
        except Exception as e:
            logger.warning("voiceprint delete error: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        return web.json_response({"ok": resp.ok, "deleted_templates": resp.deleted_templates,
                                  "deleted_memories": resp.deleted_memories})

    # ── 视觉单帧（M4 P4）────────────────────────────────────────────────────
    # 帧只在网关内存里活 TTL 秒，不落 Redis 不落盘；对话链里流动的只有 frame_id。
    @routes.post("/api/vision/frame")
    async def handle_vision_frame(request: web.Request):
        """HMI 命中视觉触发词时抓的一帧。body=二进制图像；query: mime。返回短 TTL frame_id。"""
        import vision_frames
        data = await request.read()
        if not data:
            return web.json_response({"error": "empty frame"}, status=400)
        if len(data) > vision_frames.max_bytes():
            # 超限直接拒绝而不是静默截断——半张图会让模型一本正经地答错。
            return web.json_response({"error": "frame too large",
                                      "max_bytes": vision_frames.max_bytes()}, status=413)
        mime = (request.query.get("mime") or "image/jpeg").strip()
        fid = vision_frames.store().put(data, mime)
        logger.info("vision frame stored: %s %d bytes %s", fid, len(data), mime)
        return web.json_response({"frame_id": fid, "ttl_s": vision_frames.ttl_s(),
                                  "bytes": len(data)})

    @routes.get("/api/vision/info")
    async def handle_vision_info(request: web.Request):
        """能力探测（HMI 据此决定要不要抓帧）。

        **不赌当前 active 大脑能看图**：能看图的是特定的 VL 型号，而 active 是用户随时会切的
        （切到 DeepSeek 就完全没有视觉）。故视觉走**请求级 pin**（D2 既有机制）显式指定
        `VISION_PROVIDER`/`VISION_MODEL`，本端点只回答「那个型号配没配、可不可用」。
        """
        import vision_frames
        prov = (os.getenv("VISION_PROVIDER") or "").strip() or "qwen-vl"
        ok = get_runtime().provider_available(prov)
        return web.json_response({
            "enabled": ok, "provider": prov,
            "model": (os.getenv("VISION_MODEL") or "").strip() or "qwen3-vl-plus",
            "reason": "" if ok else f"{prov} 未配置 key",
            "cached_frames": len(vision_frames.store()), "ttl_s": vision_frames.ttl_s(),
        })

    @routes.get("/api/llm/providers")
    async def handle_llm_providers(request: web.Request):
        """列出已装配的 LLM 厂商 + 各自模型 + 可用性 + 当前 active（HMI 设置页两级选择据此渲染）。"""
        return web.json_response(get_runtime().status())

    @routes.post("/api/llm/probe")
    async def handle_llm_probe(request: web.Request):
        """按需体检指定厂商（缺省=active）：1 条小请求返回 ok/latency，并记入 health。
        body: {provider?}。刻意无周期探活（不烧付费 token），演示前手动点检用。"""
        try:
            body = await request.json()
        except Exception:
            body = {}
        result = await get_runtime().probe((body.get("provider") or "").strip())
        return web.json_response(result, status=200 if result.get("ok") else 502)

    @routes.post("/api/llm/provider")
    async def handle_llm_set_provider(request: web.Request):
        """切换全局 active LLM 厂商/模型（所有服务的 LLM 调用随之切换）。body: {provider, model?}。"""
        try:
            body = await request.json()
        except Exception:
            body = {}
        provider = (body.get("provider") or "").strip()
        model = (body.get("model") or "").strip()
        if not provider:
            return web.json_response({"error": "missing provider"}, status=400)
        try:
            status = get_runtime().set_active(provider, model)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(status)

    @routes.get("/api/s2s/info")
    async def handle_s2s_info(request: web.Request):
        """S2S 能力探测（HMI 设置页据此渲染「语音链路」挡位可用性）。

        默认 classic——原始音频上云是隐私口径变化点（RFC §5.4），必须用户显式选择。"""
        from s2s import s2s_available
        return web.json_response({
            "available": s2s_available(),
            "default": "classic",  # §6.1 常态双链路，s2s 挡位不默认开
            "provider": os.getenv("S2S_PROVIDER", "dashscope"),
            "model": os.getenv("S2S_MODEL", "qwen3.5-omni-flash-realtime"),
            "voices": ["Tina", "Cherry", "Chelsie", "Serena", "Ethan"],
            "out_sample_rate": 24000,
        })

    @routes.get("/api/s2s")
    async def handle_s2s(request: web.Request):
        """S2S 全双工会话（M4 RFC §2.1 通道拓扑：落 llm-gateway 音频面，不经 edge-gateway）。

        上行：{type:session.start,…} / {type:audio}+二进制 PCM 帧 / {type:barge_in} /
              {type:cancel_turn} / {type:escalated_result,turn_id,text} / {type:session.end}
        下行：turn.transcript / turn.answer_delta / turn.audio_meta+二进制 PCM /
              turn.end / turn.escalated / session.state / unsupported

        代价与补偿：edge-gateway 看不见 S2S 轮次 → 文本副产品回灌为**强制项**（§7）。
        """
        from s2s import (
            S2SSession, Reflux, build_context_summary, build_s2s_provider,
            UP_AUDIO, UP_AUDIO_DONE, UP_BARGE_IN, UP_CANCEL_TURN, UP_ESCALATED_RESULT,
            UP_OCCUPANT, UP_SESSION_END, UP_SESSION_START, DOWN_UNSUPPORTED,
        )

        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        session: "S2SSession | None" = None

        async def emit_json(obj: dict):
            if not ws.closed:
                try:
                    await ws.send_json(obj)
                except Exception:
                    pass

        async def emit_audio(pcm: bytes):
            if not ws.closed:
                try:
                    await ws.send_bytes(pcm)
                except Exception:
                    pass

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    if session is not None:
                        await session.push_audio(bytes(msg.data))
                    continue
                if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE,
                                aiohttp.WSMsgType.CLOSING):
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                mtype = data.get("type")

                if mtype == UP_SESSION_START:
                    if session is not None:
                        continue  # 幂等：一条 WS 一个会话
                    resolved_user = str(data.get("user_id") or "")
                    resolved_vehicle = str(data.get("vehicle_id") or "")
                    if os.getenv("E2E_IDENTITY_ENABLED", "").lower() == "true":
                        from e2e_identity import (
                            IdentityTokenError,
                            resolve_s2s_identity,
                        )
                        try:
                            resolved_user, resolved_vehicle, _ = resolve_s2s_identity(
                                data,
                            )
                        except IdentityTokenError:
                            # session.start carries the token, so the HTTP Upgrade
                            # has already happened. Close before provider/session
                            # construction and never echo the token or secret.
                            await ws.close(
                                code=1008,
                                message=b"unauthorized test identity",
                            )
                            break
                    sid = str(data.get("session_id") or "")
                    if _set_obs_session and sid:
                        _set_obs_session(sid)
                    prov_name = (data.get("provider") or "").strip()
                    model = (data.get("model") or "").strip()
                    vad_ms = int(data.get("vad_silence_ms") or 0)
                    try:
                        probe = build_s2s_provider(prov_name, model, vad_silence_ms=vad_ms)
                    except ValueError as e:  # 型号不支持 tools → fail-fast，不静默降级
                        logger.warning("S2S 型号被拒: %s", e)
                        await emit_json({"type": DOWN_UNSUPPORTED, "message": str(e)})
                        continue
                    if probe is None:
                        await emit_json({"type": DOWN_UNSUPPORTED,
                                         "message": "S2S 未配置或无凭据"})
                        continue
                    reflux = Reflux(
                        memory_stub_getter=_memory_stub, obs=_obs,
                        gate_content=_gate_content, session_id=sid,
                        user_id=resolved_user,
                        vehicle_id=resolved_vehicle,
                        occupant_id=str(data.get("occupant_id") or ""),
                        provider_name=prov_name or os.getenv("S2S_PROVIDER", "dashscope"),
                        model=model or os.getenv("S2S_MODEL", "qwen3.5-omni-flash-realtime"))
                    session = S2SSession(
                        provider_factory=lambda: build_s2s_provider(
                            prov_name, model, vad_silence_ms=vad_ms),
                        emit_json=emit_json, emit_audio=emit_audio,
                        # owner 在调用时现取（M-B）：说话人是**唤醒粒度**的，
                        # `occupant` 上行帧会热更 reflux——重注入必须跟着同一个 owner 走，
                        # 否则换人后回灌归属对了、重注入材料还是上一位的对话。
                        context_provider=lambda: build_context_summary(
                            _memory_stub(), sid, user_id=reflux.user_id,
                            occupant_id=reflux.occupant_id),
                        reflux=reflux, session_id=sid,
                        user_id=resolved_user,
                        voice=(data.get("voice") or "").strip())
                    try:
                        await session.start()
                    except Exception as e:
                        logger.warning("S2S 会话建立失败: %s", e)
                        await emit_json({"type": DOWN_UNSUPPORTED, "message": str(e)[:200]})
                        session = None
                    continue

                if session is None:
                    continue
                if mtype == UP_AUDIO_DONE:
                    await session.audio_done()
                elif mtype == UP_BARGE_IN:
                    await session.barge_in()
                elif mtype == UP_CANCEL_TURN:
                    await session.cancel_turn()
                elif mtype == UP_ESCALATED_RESULT:
                    await session.escalated_result(str(data.get("turn_id") or ""),
                                                   str(data.get("text") or ""))
                elif mtype == UP_OCCUPANT:
                    # M4 P4 补口（验收抓到）：会话级静态快照会把 S2S 自答轮全记进
                    # primary——说话人是唤醒粒度的，声纹识别落地即更新回灌归属。
                    reflux.occupant_id = str(data.get("occupant_id") or "primary")
                elif mtype == UP_SESSION_END:
                    break
                elif mtype == UP_AUDIO:
                    pass  # 声明式前导帧，后跟二进制（与 /api/asr/stream 同惯例）
        finally:
            if session is not None:
                await session.close()
        return ws

    @routes.get("/api/health")
    async def handle_health(request: web.Request):
        return web.json_response({"status": "ok", "service": "audio-http"})

    app = web.Application()
    app.add_routes(routes)

    # CORS：允许 HMI 跨域调用（HMI 在 :5173/:3000，本面在 :50059，永远是跨域）。
    # **白名单必须覆盖本 app 注册的每一个方法**——2026-07-26 真机：声纹删除是全 HMI 唯一的
    # DELETE，方法漏在白名单外 → 浏览器 preflight 直接挡下，请求根本没发出来，服务端零日志、
    # e2e 也测不出来（e2e 从服务端发请求，不过 CORS）。契约测试 `test_http_cors.py` 钉死。
    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = CORS_METHODS
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    app.middlewares.append(cors_middleware)
    return app


async def start_http_server():
    port = int(os.getenv("AUDIO_HTTP_PORT", "50059"))
    app = create_http_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[llm-gateway] Audio HTTP proxy on :{port} (/api/asr, /api/tts, /api/voices)", flush=True)
