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

import grpc
from aiohttp import web

from cockpit.memory.v1 import memory_pb2, memory_pb2_grpc

from providers import build_asr_provider, build_tts_provider

logger = logging.getLogger("llm.http")

_WAV_FORMATS = frozenset({"wav", "pcm", "pcm16"})


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
        _mem_channel = grpc.aio.insecure_channel(MEMORY_ADDR)
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
        """查询可用音色列表。"""
        lang = request.query.get("language", "")
        gender = request.query.get("gender", "")
        voices = await tts.list_voices(language=lang, gender=gender)
        return web.json_response({"voices": voices})

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
