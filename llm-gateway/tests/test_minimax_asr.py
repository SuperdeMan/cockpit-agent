"""MiniMax ASR（speech_to_text）接入单测——全部离线可跑。

设计见 docs/design/2026-09-14-minimax-asr-provider.md。三层各自钉住：
① `MiniMaxASRProvider` 的表单装配（Bearer / language 头有无 / 字段 / 文件名与 MIME / 占位 WAV 回填 /
   裸 PCM 套头 / 模型归一）与 json / SSE 两种响应解析、非 2xx 可诊断；
② `WholeUtteranceASRProvider` 整句适配：<0.1s 零调用、松手后 partial→final、半途断流的两种结局；
③ 两个工厂 + `/api/asr/stream/info` 目录：显式 / auto / 无 key 三档，既有 auto 路径不变。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys

import httpx
from aiohttp.test_utils import TestClient, TestServer

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)

import providers as P  # noqa: E402
from providers import (  # noqa: E402
    MiniMaxASRProvider, MockASRProvider, ProviderHTTPError, StreamBridgeASRProvider,
    WholeUtteranceASRProvider, _asr_upload_file, _minimax_asr_language, _minimax_asr_model,
    _wav_fix_sizes, _wav_header, build_asr_provider, build_streaming_asr_provider,
)

_ENVS = (
    "ASR_PROVIDER", "ASR_STREAM_PROVIDER", "LLM_PROVIDER", "LLM_API_KEY",
    "DASHSCOPE_ASR_KEY", "LLM_EMBED_API_KEY", "MINIMAX_API_KEY",
    "MINIMAX_ASR_MODEL", "MINIMAX_ASR_URL", "REQUIRE_REAL_PROVIDERS",
)


def _clean_env(monkeypatch):
    for k in _ENVS:
        monkeypatch.delenv(k, raising=False)


# ── 桩：httpx.AsyncClient 的 post / stream 两个面 ─────────────────────────

class _Resp:
    def __init__(self, status: int, body, headers=None):
        self.status_code = status
        self._body = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        self.headers = headers or {}

    async def aread(self):
        return self._body

    def json(self):
        return json.loads(self._body)


class _StreamCtx:
    def __init__(self, status: int, lines: list[str], body: bytes = b""):
        self.status_code = status
        self._lines = lines
        self._body = body
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Client:
    """记录最后一次调用的 (url, headers, data, files)；post 回预置响应、stream 回预置 SSE 行。"""

    def __init__(self, resp=None, lines=None, stream_status=200, stream_body=b""):
        self.resp = resp
        self.lines = lines or []
        self.stream_status = stream_status
        self.stream_body = stream_body
        self.calls: list[dict] = []

    async def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "data": data, "files": files})
        return self.resp

    def stream(self, method, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data, "files": files})
        return _StreamCtx(self.stream_status, self.lines, self.stream_body)


def _provider(client, **kw) -> MiniMaxASRProvider:
    p = MiniMaxASRProvider("mm-key", **kw)
    p._client = client
    return p


_PCM = b"\x01\x00" * 16000  # 1s @16k s16le


# ── ① 表单装配与响应解析 ────────────────────────────────────────────────

def test_transcribe_request_shape_and_json_parse(monkeypatch):
    _clean_env(monkeypatch)
    client = _Client(resp=_Resp(200, {"text": " 实际上还是商家赚了 ", "duration": 26.325, "trace_id": "t1"}))
    prov = _provider(client)
    wav = _wav_header(len(_PCM)) + _PCM
    out = asyncio.run(prov.transcribe(audio=wav, fmt="wav", language="zh", model="mimo-v2.5-asr"))

    assert out == ("实际上还是商家赚了", 0.9, "zh", "asr-1.0", 26325)
    call = client.calls[0]
    assert call["url"] == "https://api.minimaxi.com/v1/speech_to_text"
    assert call["headers"] == {"Authorization": "Bearer mm-key", "language": "zh"}
    # 批处理面每次都传 ASR_MODEL（mimo-v2.5-asr）——必须归一成 MiniMax 自家 id，不能原样送出去
    assert call["data"] == {"model": "asr-1.0", "response_format": "json", "stream": "false"}
    name, data, mime = call["files"]["file"]
    assert (name, mime) == ("audio.wav", "audio/wav")
    assert data == wav  # 标准 WAV 逐字节不动


def test_language_header_omitted_for_auto_and_region_tag_trimmed(monkeypatch):
    _clean_env(monkeypatch)
    assert _minimax_asr_language("auto") == ""
    assert _minimax_asr_language("") == ""
    assert _minimax_asr_language("zh-CN") == "zh"
    assert _minimax_asr_language("EN") == "en"
    assert _minimax_asr_language("yue") == "yue"

    client = _Client(resp=_Resp(200, {"text": "ok", "duration": 1}))
    prov = _provider(client)
    asyncio.run(prov.transcribe(audio=_PCM, fmt="pcm16", language="auto", model=""))
    assert "language" not in client.calls[0]["headers"]  # 不发头 = 混合语言识别


def test_model_normalization(monkeypatch):
    _clean_env(monkeypatch)
    assert _minimax_asr_model("") == "asr-1.0"
    assert _minimax_asr_model("qwen3-asr-flash-realtime-2026-02-10") == "asr-1.0"  # mobile 备用模型重试带来的 id
    assert _minimax_asr_model("asr-1.0") == "asr-1.0"
    assert _minimax_asr_model("ASR-2.0") == "ASR-2.0"  # 自家新 id 原样放行
    monkeypatch.setenv("MINIMAX_ASR_MODEL", "asr-1.5")  # 刻意没有 env 旋钮（见 providers 注释）
    assert _minimax_asr_model("mimo-v2.5-asr") == "asr-1.0"

    client = _Client(resp=_Resp(200, {"text": "ok", "duration": 1}))
    prov = _provider(client, model="asr-1.5")
    # 请求级 asr-* 盖过实例默认；别家 id 落回实例默认
    asyncio.run(prov.transcribe(audio=_PCM, fmt="pcm", language="zh", model="asr-9"))
    assert client.calls[-1]["data"]["model"] == "asr-9"
    asyncio.run(prov.transcribe(audio=_PCM, fmt="pcm", language="zh", model="fun-asr-realtime"))
    assert client.calls[-1]["data"]["model"] == "asr-1.5"


def test_url_is_fixed_unless_passed_explicitly(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_ASR_URL", "https://proxy.example/v1/speech_to_text")  # 刻意没有 env 旋钮
    assert MiniMaxASRProvider("k").url == "https://api.minimaxi.com/v1/speech_to_text"
    assert MiniMaxASRProvider("k", url="https://x/y").url == "https://x/y"


def test_raw_pcm_gets_wav_header_and_other_containers_keep_extension():
    name, data, mime = _asr_upload_file(_PCM, "pcm16le")
    assert (name, mime) == ("audio.wav", "audio/wav")
    assert data == _wav_header(len(_PCM)) + _PCM  # MiniMax 不收裸 PCM
    name, data, mime = _asr_upload_file(b"\xff\xf3", "mp3")
    assert (name, data, mime) == ("audio.mp3", b"\xff\xf3", "audio/mpeg")
    name, data, mime = _asr_upload_file(b"OggS", ".opus")
    assert (name, mime) == ("audio.opus", "audio/ogg")


def test_ffmpeg_pipe_wav_placeholder_sizes_are_backfilled():
    good = _wav_header(len(_PCM)) + _PCM
    pipe = bytearray(good)
    pipe[4:8] = b"\xff\xff\xff\xff"        # RIFF size 占位
    pipe[40:44] = b"\x00\x00\x00\x00"      # data size 占位（0）
    assert _wav_fix_sizes(bytes(pipe)) == good
    assert _wav_fix_sizes(good) == good     # 标准 WAV 不动
    assert _wav_fix_sizes(_PCM) == _PCM     # 非 RIFF 原样
    # 只回填占位的那个字段：RIFF 占位而 data 真实 → data 不动
    half = bytearray(good)
    half[4:8] = b"\x00\x00\x00\x00"
    fixed = _wav_fix_sizes(bytes(half))
    assert fixed[4:8] == good[4:8] and fixed[40:44] == good[40:44]


def test_http_error_carries_status_body_and_retry_after(monkeypatch):
    _clean_env(monkeypatch)
    client = _Client(resp=_Resp(429, {"error": {"type": "rate_limit_error", "message": "slow down"}},
                                headers={"retry-after": "3"}))
    prov = _provider(client)
    try:
        asyncio.run(prov.transcribe(audio=_PCM, fmt="pcm", language="zh", model=""))
        assert False, "should raise"
    except ProviderHTTPError as e:
        assert e.status_code == 429 and e.retry_after == 3.0
        assert "rate_limit_error" in str(e)


def test_business_error_inside_200_is_not_silently_empty(monkeypatch):
    _clean_env(monkeypatch)
    client = _Client(resp=_Resp(200, {"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}}))
    prov = _provider(client)
    try:
        asyncio.run(prov.transcribe(audio=_PCM, fmt="pcm", language="zh", model=""))
        assert False, "should raise"
    except RuntimeError as e:
        assert "1004" in str(e)


def test_transcribe_stream_parses_sse_in_order_and_stops_at_finish(monkeypatch):
    _clean_env(monkeypatch)
    lines = [
        ": keep-alive",
        'data: {"index":0,"delta":"实际上","finish":false}',
        "",
        "data:",
        'data: {"index":1,"delta":"还是商家赚了","finish":false}',
        "",
        "data: not-json",
        'data: {"index":2,"delta":"","finish":true,"duration":26.325}',
        'data: {"index":3,"delta":"finish 之后不该再读","finish":false}',
    ]
    client = _Client(lines=lines)
    prov = _provider(client)

    async def collect():
        return [ev async for ev in prov.transcribe_stream(audio=_PCM, fmt="pcm", language="zh", model="")]

    assert asyncio.run(collect()) == [("实际上", False, 0.0), ("还是商家赚了", False, 0.0), ("", True, 26.325)]
    call = client.calls[0]
    assert call["method"] == "POST"
    assert call["data"] == {"model": "asr-1.0", "response_format": "json", "stream": "true"}


def test_transcribe_stream_http_error(monkeypatch):
    _clean_env(monkeypatch)
    client = _Client(stream_status=422, stream_body=b'{"error":{"type":"unprocessable_entity_error"}}')
    prov = _provider(client)

    async def collect():
        return [ev async for ev in prov.transcribe_stream(audio=_PCM, fmt="pcm", language="zh", model="")]

    try:
        asyncio.run(collect())
        assert False, "should raise"
    except ProviderHTTPError as e:
        assert e.status_code == 422 and "unprocessable" in str(e)


# ── ② 整句适配（流式插槽）──────────────────────────────────────────────

class _FakeBatchWithStream:
    """有 transcribe_stream 的批引擎（= MiniMax）。记录收到的 WAV。"""
    model = "asr-1.0"

    def __init__(self, events, calls=None):
        self.events = events
        self.audio = []
        self.batch_calls = 0

    async def transcribe(self, audio, fmt, language, model):
        self.batch_calls += 1
        return "不该走这条", 0.9, language, model, 0

    async def transcribe_stream(self, audio, fmt, language, model):
        self.audio.append((audio, fmt, language, model))
        for ev in self.events:
            yield ev


class _FakeBatchPlain:
    """没有 transcribe_stream 的批引擎——一次 transcribe 出定稿。"""
    model = "plain-1"

    def __init__(self):
        self.audio = []

    async def transcribe(self, audio, fmt, language, model):
        self.audio.append((audio, fmt, language, model))
        return " 打开空调 ", 0.9, language, model, 1000


async def _frames(pcm: bytes, step: int = 3200):
    for i in range(0, len(pcm), step):
        yield pcm[i:i + step]


def _run_stream(engine, pcm: bytes, language="zh"):
    async def go():
        return [ev async for ev in engine.stream(_frames(pcm), language=language)]
    return asyncio.run(go())


def test_whole_utterance_partial_then_final_after_stream_end():
    batch = _FakeBatchWithStream([("实际上", False, 0.0), ("还是商家赚了", False, 0.0), ("", True, 26.325)])
    engine = WholeUtteranceASRProvider(batch)
    assert engine.model == "asr-1.0"

    evs = _run_stream(engine, _PCM)

    assert evs == [{"text": "实际上", "final": False},
                   {"text": "实际上还是商家赚了", "final": False},
                   {"text": "实际上还是商家赚了", "final": True}]
    assert batch.batch_calls == 0
    (audio, fmt, language, model), = batch.audio  # 只打了一次
    assert audio == _wav_header(len(_PCM)) + _PCM and fmt == "wav" and language == "zh" and model == "asr-1.0"


def test_whole_utterance_short_press_costs_nothing():
    batch = _FakeBatchWithStream([("x", True, 0.0)])
    evs = _run_stream(WholeUtteranceASRProvider(batch), b"\x00" * 3198)  # <0.1s
    assert evs == [{"text": "", "final": True}]
    assert batch.audio == [] and batch.batch_calls == 0


def test_whole_utterance_falls_back_to_plain_transcribe():
    batch = _FakeBatchPlain()
    engine = WholeUtteranceASRProvider(batch)
    assert engine.model == "plain-1"
    evs = _run_stream(engine, _PCM, language="en")
    assert evs == [{"text": "打开空调", "final": True}]
    assert batch.audio[0][1:] == ("wav", "en", "plain-1")


def test_whole_utterance_finish_event_with_text_is_kept():
    """终止事件 delta 非空也要拼进定稿（文档示例为空，但不赌）。"""
    batch = _FakeBatchWithStream([("打开", False, 0.0), ("空调", True, 1.0)])
    evs = _run_stream(WholeUtteranceASRProvider(batch), _PCM)
    assert evs == [{"text": "打开", "final": False}, {"text": "打开空调", "final": True}]


def test_whole_utterance_sse_cut_without_text_raises_for_client_fallback():
    batch = _FakeBatchWithStream([])  # 半途断流、一个字没给
    try:
        _run_stream(WholeUtteranceASRProvider(batch), _PCM)
        assert False, "should raise"
    except RuntimeError as e:
        assert "无转写" in str(e)


def test_whole_utterance_sse_cut_with_text_finalizes_what_arrived():
    batch = _FakeBatchWithStream([("打开", False, 0.0), ("空调", False, 0.0)])  # 没等到 finish
    evs = _run_stream(WholeUtteranceASRProvider(batch), _PCM)
    assert evs[-1] == {"text": "打开空调", "final": True}


def test_whole_utterance_engine_error_propagates():
    class _Boom(_FakeBatchWithStream):
        async def transcribe_stream(self, audio, fmt, language, model):
            raise ProviderHTTPError(422, "sensitive")
            yield  # noqa: async generator 标记

    try:
        _run_stream(WholeUtteranceASRProvider(_Boom([])), _PCM)
        assert False, "should raise"
    except ProviderHTTPError as e:
        assert e.status_code == 422


# ── ③ 工厂与目录 ────────────────────────────────────────────────────────

def test_streaming_factory_minimax_wraps_batch_engine(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    eng = build_streaming_asr_provider("minimax", "qwen3-asr-flash-realtime-2026-02-10", vad_silence_ms=500)
    assert isinstance(eng, WholeUtteranceASRProvider)
    assert isinstance(eng.batch, MiniMaxASRProvider)
    assert eng.model == "asr-1.0"  # 客户端带来的别家 id 已归一
    assert eng.batch.api_key == "mmk"


def test_streaming_factory_minimax_without_key_is_unsupported(monkeypatch):
    _clean_env(monkeypatch)
    assert build_streaming_asr_provider("minimax") is None


def test_batch_factory_explicit_minimax(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ASR_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    monkeypatch.setenv("LLM_PROVIDER", "xiaomimimo")
    monkeypatch.setenv("LLM_API_KEY", "mk")  # 有 MiMo key 也钉住 MiniMax
    prov = build_asr_provider()
    assert isinstance(prov, MiniMaxASRProvider) and prov.provider == "minimax"


def test_batch_factory_explicit_minimax_without_key_mock(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ASR_PROVIDER", "minimax")
    assert isinstance(build_asr_provider(), MockASRProvider)


def test_batch_factory_auto_follows_minimax_stream_engine(monkeypatch):
    """auto：chat 不是 MiMo + ASR_STREAM_PROVIDER=minimax + 有 key → 批处理跟随（同 TTS auto 惯例）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("ASR_STREAM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    monkeypatch.setenv("LLM_EMBED_API_KEY", "bailian")  # 百炼 key 也在，但显式选了 minimax
    assert isinstance(build_asr_provider(), MiniMaxASRProvider)


def test_batch_factory_auto_mimo_still_wins_over_minimax_stream_choice(monkeypatch):
    """历史现状不变：LLM_PROVIDER=mimo 系 + 有 key 的 auto 仍是 MiMo，哪怕流式选了 minimax。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "xiaomimimo")
    monkeypatch.setenv("LLM_API_KEY", "mk")
    monkeypatch.setenv("ASR_STREAM_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    assert isinstance(build_asr_provider(), P.MiMoASRProvider)


def test_batch_factory_auto_without_minimax_choice_unchanged(monkeypatch):
    """有 MINIMAX_API_KEY 但流式没选 minimax → auto 逐字走老路（dashscope 桥接 / mock）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    assert isinstance(build_asr_provider(), MockASRProvider)
    monkeypatch.setenv("LLM_EMBED_API_KEY", "bailian")
    prov = build_asr_provider()
    assert isinstance(prov, StreamBridgeASRProvider) and prov.provider == "dashscope"


def test_stream_info_lists_minimax_keyed_on_the_shared_key(monkeypatch):
    _clean_env(monkeypatch)
    spec = importlib.util.spec_from_file_location(
        "llm_gateway_http_server_minimax_asr_test", os.path.join(_DIR, "http_server.py"))
    HS = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(HS)

    async def probe():
        async with TestClient(TestServer(HS.create_http_app())) as client:
            resp = await client.get("/api/asr/stream/info")
            return await resp.json()

    off = asyncio.run(probe())
    entry = next(p for p in off["providers"] if p["id"] == "minimax")
    assert entry == {"id": "minimax", "label": "MiniMax 整句", "available": False, "mode": "utterance",
                     "models": ["asr-1.0"], "model_labels": {"asr-1.0": "MiniMax asr-1.0"}}
    assert off["streaming"] is False

    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    on = asyncio.run(probe())
    assert next(p for p in on["providers"] if p["id"] == "minimax")["available"] is True
    assert on["streaming"] is True
    # 「方式 → 引擎」目录契约（HMI / mobile 共用）：mode 每条都有、模型 id 全小写、两个方式都声明
    assert [(p["id"], p["mode"]) for p in on["providers"]] == [
        ("dashscope", "realtime"), ("minimax", "utterance"), ("mimo", "utterance")]
    assert [m["id"] for m in on["modes"]] == ["realtime", "utterance"]
    for p in on["providers"]:
        assert p["models"] == [m.lower() for m in p["models"]]
        assert set(p["model_labels"]) == set(p["models"])
    assert next(p for p in on["providers"] if p["id"] == "mimo")["label"] == "MiMo 整句"


def test_streaming_factory_mimo_is_whole_utterance_now_and_chunked_stays_env_alias(monkeypatch):
    """MiMo 与 MiniMax 同一形态（文件转写 API）⇒ 目录里的 `mimo` 走同一整句适配；旧伪 partial 只剩 env 别名。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "mk")
    eng = build_streaming_asr_provider("mimo", "")
    assert isinstance(eng, WholeUtteranceASRProvider) and isinstance(eng.batch, P.MiMoASRProvider)
    assert eng.model == "mimo-v2.5-asr"
    assert isinstance(build_streaming_asr_provider("mimo-chunked", ""), P.MiMoChunkedASRProvider)
    _clean_env(monkeypatch)
    assert build_streaming_asr_provider("mimo", "") is None


def _load_http_server():
    spec = importlib.util.spec_from_file_location(
        "llm_gateway_http_server_minimax_asr_ws_test", os.path.join(_DIR, "http_server.py"))
    HS = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(HS)
    return HS


def _drive_ws(HS, start_frame: dict) -> list[dict]:
    """按客户端协议驱动真 handle_asr_stream：start → PCM 二进制帧 → stop → 收到终态帧为止。"""
    async def go():
        async with TestClient(TestServer(HS.create_http_app())) as client:
            async with client.ws_connect("/api/asr/stream") as ws:
                await ws.send_json(start_frame)
                for i in range(0, len(_PCM), 3200):
                    await ws.send_bytes(_PCM[i:i + 3200])
                await ws.send_json({"type": "stop"})
                msgs = []
                while True:
                    m = await ws.receive_json(timeout=5)
                    msgs.append(m)
                    if m["type"] in ("done", "error", "unsupported"):
                        return msgs
    return asyncio.run(go())


def test_ws_stream_end_to_end_minimax_engine_only_the_http_call_is_faked(monkeypatch):
    """整条链：WS 传输层（PCM 直传 → 队列 → 流末）→ WholeUtterance 攒段 → MiniMax SSE → partial/final/done。
    只桩掉出站 HTTP；网关端点、工厂、适配器、provider 全是真代码。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    fake = _Client(lines=['data: {"index":0,"delta":"实际上","finish":false}', "",
                          'data: {"index":1,"delta":"还是商家赚了","finish":false}', "",
                          'data: {"index":2,"delta":"","finish":true,"duration":1.0}'])
    monkeypatch.setattr(P.MiniMaxASRProvider, "_http", lambda self: fake)
    HS = _load_http_server()

    msgs = _drive_ws(HS, {"type": "start", "format": "pcm16le", "sample_rate": 16000,
                          "language": "zh", "provider": "minimax", "model": ""})

    assert msgs == [{"type": "partial", "text": "实际上"},
                    {"type": "partial", "text": "实际上还是商家赚了"},
                    {"type": "final", "text": "实际上还是商家赚了"},
                    {"type": "done"}]
    # 出站只有一次、且是整段：WAV 头 + 全部 PCM 帧按序拼回
    (call,) = fake.calls
    assert call["data"]["stream"] == "true" and call["data"]["model"] == "asr-1.0"
    assert call["headers"]["language"] == "zh"
    assert call["files"]["file"][1] == _wav_header(len(_PCM)) + _PCM


def test_ws_stream_minimax_without_key_answers_unsupported_so_clients_fall_back(monkeypatch):
    _clean_env(monkeypatch)
    HS = _load_http_server()
    msgs = _drive_ws(HS, {"type": "start", "format": "pcm16le", "sample_rate": 16000,
                          "language": "zh", "provider": "minimax", "model": ""})
    assert msgs == [{"type": "unsupported"}]


def test_ws_stream_minimax_http_failure_surfaces_as_error_frame(monkeypatch):
    """422（内容风控）等上游失败 → error 帧（客户端据此回退批处理），而不是挂到 7s 兜底。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    fake = _Client(stream_status=422, stream_body=b'{"error":{"type":"unprocessable_entity_error"}}')
    monkeypatch.setattr(P.MiniMaxASRProvider, "_http", lambda self: fake)
    HS = _load_http_server()
    msgs = _drive_ws(HS, {"type": "start", "format": "pcm16le", "sample_rate": 16000,
                          "language": "zh", "provider": "minimax", "model": ""})
    assert msgs[-1]["type"] == "error" and "422" in msgs[-1]["message"]


def test_real_httpx_multipart_encoding_matches_the_documented_form():
    """不用桩：让真 httpx 把 (headers, data, files) 编成请求，核对线上的 multipart 形状。"""
    prov = MiniMaxASRProvider("mm-key")
    headers, form, files = prov._request(_PCM, "pcm16le", "zh-CN", "", stream=True)
    req = httpx.Request("POST", prov.url, headers=headers, data=form, files=files)
    body = req.read()
    ctype = req.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary=")
    assert req.headers["authorization"] == "Bearer mm-key" and req.headers["language"] == "zh"
    assert b'name="model"\r\n\r\nasr-1.0' in body
    assert b'name="stream"\r\n\r\ntrue' in body
    assert b'name="response_format"\r\n\r\njson' in body
    assert b'name="file"; filename="audio.wav"\r\nContent-Type: audio/wav' in body
    assert b"RIFF" in body and _PCM[:64] in body
