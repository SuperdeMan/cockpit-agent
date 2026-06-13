"""HTTP 代理层：让 HMI 前端能调用 ASR/TTS 服务。

HMI 是浏览器环境，不能直接调 gRPC。此模块在 llm-gateway 同进程内启动一个
轻量 HTTP server，暴露 /api/asr 和 /api/tts 端点。

端口：LLM_GATEWAY_PORT + 1（默认 50053，但与 memory 冲突，用 50059）。
"""
from __future__ import annotations
import base64
import json
import logging
import os

from aiohttp import web

from providers import build_asr_provider, build_tts_provider

logger = logging.getLogger("llm.http")

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
