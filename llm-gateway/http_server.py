"""HTTP 代理层：让 HMI 前端能调用 ASR/TTS 服务。

HMI 是浏览器环境，不能直接调 gRPC。此模块在 llm-gateway 同进程内启动一个
轻量 HTTP server，暴露 /api/asr 和 /api/tts 端点。

端口：LLM_GATEWAY_PORT + 1（默认 50053，但与 memory 冲突，用 50059）。
"""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import os

import aiohttp
import grpc
from aiohttp import web

from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from runtime.grpcio import aio_channel

from providers import (
    build_asr_provider, build_streaming_asr_provider, build_tts_provider,
    build_tts_stream_provider, TTS_STREAM_CATALOG,
)
from llm_runtime import get_runtime

logger = logging.getLogger("llm.http")

# 观测指标（best-effort，无 NATS/observability 不可导入时自动降级 log）。
try:
    from observability.events import get_emitter
    _obs = get_emitter("llm-gateway")
except Exception:  # observability 不在 path（如精简镜像）→ log-only
    _obs = None

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


def _memory_stub():
    """memory gRPC 客户端（懒连接、复用）。HMI 是浏览器、不能直连 gRPC，
    经本 HTTP 代理读记忆内容（只读）。"""
    global _mem_channel
    if _mem_channel is None:
        _mem_channel = aio_channel(MEMORY_ADDR)
    return memory_pb2_grpc.MemoryStub(_mem_channel)

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
            text = body.get("text", "")
            if not text:
                return web.json_response({"error": "missing text"}, status=400)

            voice_id = body.get("voice_id", DEFAULT_VOICE)
            fmt = body.get("format", "wav")

            audio_bytes, fmt_out, dur, model, voice = await tts.synthesize(
                text=text, voice_id=voice_id, model=DEFAULT_TTS_MODEL,
                speed=1.0, fmt=fmt)

            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
            logger.info("TTS: %d chars -> %d bytes, voice=%s", len(text), len(audio_bytes), voice)
            return web.json_response({
                "audio": audio_b64, "format": fmt_out, "duration_ms": dur,
                "model": model, "voice_id": voice,
            })
        except Exception as e:
            import traceback
            logger.warning("TTS error: %s\n%s", e, traceback.format_exc())
            return web.json_response({"error": str(e)}, status=500)

    @routes.get("/api/voices")
    async def handle_voices(request: web.Request):
        """查询某引擎的音色列表。?provider=cosyvoice|qwen|mimo（默认 mimo，向后兼容）。"""
        provider = request.query.get("provider", "mimo").strip().lower()
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
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        ffmpeg = None
        tasks: list = []
        pcm_queue: asyncio.Queue = asyncio.Queue()
        pcm_direct = False  # True=前端直传 s16le PCM（跳 ffmpeg，B1）
        started = False     # 已收到 start（防重复；PCM 模式无 ffmpeg 句柄可判）

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
            try:
                async for r in provider.stream(pcm_iter(), language=language):
                    if ws.closed:
                        break
                    await ws.send_json({"type": "final" if r.get("final") else "partial",
                                        "text": r.get("text", "")})
                if not ws.closed:
                    await ws.send_json({"type": "done"})
            except Exception as e:
                logger.warning("ASR stream provider error: %s", e)
                if not ws.closed:
                    await ws.send_json({"type": "error", "message": str(e)})

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

        async def run_provider(provider, voice, sample_rate):
            t0 = time.monotonic()
            first_ms = None
            chunks = 0
            err = None
            try:
                async for out in provider.stream(text_iter(), voice=voice, sample_rate=sample_rate):
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
                        tasks.append(asyncio.create_task(run_provider(
                            provider, data.get("voice", ""), int(data.get("sample_rate") or 0))))
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
        if not sid:
            return web.json_response({"turns": []})
        try:
            resp = await _memory_stub().GetSession(
                memory_pb2.GetSessionRequest(session_id=sid, last_n=last_n), timeout=5)
            turns = [{"role": t.role, "text": t.text, "ts": t.ts} for t in resp.turns]
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
                session_id=sid, user_id=uid, vehicle_id=vid, scopes=scopes), timeout=5)
            return web.json_response({"values": dict(resp.values)})
        except Exception as e:
            logger.warning("memory context read error: %s", e)
            return web.json_response({"values": {}, "error": str(e)})

    @routes.get("/api/memory/profile")
    async def handle_mem_profile(request: web.Request):
        """读用户**真实学到的**记忆（HMI 记忆视图）：偏好/常去地点/情景。
        走分层记忆 ExportUser（非 mock context），只取现行（未被取代）。?user_id="""
        uid = request.query.get("user_id", "")
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
                               "name": v.get("name") or v.get("address") or m.get("text", ""),
                               "address": v.get("address", ""), "scope": m.get("scope", "")})
            elif m.get("kind") == "semantic":
                prefs.append({"predicate": pred, "text": m.get("text", ""),
                              "scope": m.get("scope", ""),
                              "provenance": m.get("provenance", ""),
                              "confidence": m.get("confidence", 0)})
        return web.json_response({"preferences": prefs, "places": places, "episodes": episodes})

    @routes.post("/api/memory/forget")
    async def handle_mem_forget(request: web.Request):
        """删除用户记忆（HMI 管理）。body: {user_id, scope?}。scope 空=清空全部（GDPR 硬删）。"""
        try:
            body = await request.json()
        except Exception:
            body = {}
        uid = (body.get("user_id") or "").strip()
        scope = (body.get("scope") or "").strip()
        if not uid:
            return web.json_response({"ok": False}, status=400)
        try:
            resp = await _memory_stub().ForgetUser(memory_pb2.ForgetUserRequest(
                user_id=uid, scopes=[scope] if scope else []), timeout=5)
            return web.json_response({"ok": resp.ok, "deleted": resp.deleted})
        except Exception as e:
            logger.warning("memory forget error: %s", e)
            return web.json_response({"ok": False, "error": str(e)}, status=500)

    @routes.get("/api/llm/providers")
    async def handle_llm_providers(request: web.Request):
        """列出已装配的 LLM 厂商 + 各自模型 + 可用性 + 当前 active（HMI 设置页两级选择据此渲染）。"""
        return web.json_response(get_runtime().status())

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

    @routes.get("/api/health")
    async def handle_health(request: web.Request):
        return web.json_response({"status": "ok", "service": "audio-http"})

    app = web.Application()
    app.add_routes(routes)

    # CORS：允许 HMI 跨域调用
    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
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
