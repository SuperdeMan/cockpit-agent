"""流式 TTS provider + 工厂 单测（R4.2 P1）——全部离线可跑。

覆盖：协议帧构造（cosyvoice run-task/continue/finish、qwen session.update）、mock provider 分片产出、
工厂路由（cosyvoice/qwen/mock/off/无 key→None）、FakeWS 驱动 DashScope provider 全循环（meta+bytes+cancel）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import types

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers as P
from providers import (
    _cosyvoice_run_task, _cosyvoice_continue, _cosyvoice_finish, _qwen_session_update,
    DashScopeCosyVoiceProvider, DashScopeQwenTTSProvider, MockStreamingTTSProvider,
    build_tts_stream_provider, TTS_STREAM_CATALOG,
)


async def _aiter(items):
    for x in items:
        yield x


# ── 协议帧构造（纯函数）─────────────────────────────────────────────────────

def test_cosyvoice_run_task_frame():
    f = _cosyvoice_run_task("tid1", "cosyvoice-v3-flash", "longxiaochun_v3", 22050)
    assert f["header"] == {"action": "run-task", "task_id": "tid1", "streaming": "duplex"}
    p = f["payload"]
    assert p["task_group"] == "audio" and p["task"] == "tts" and p["function"] == "SpeechSynthesizer"
    assert p["model"] == "cosyvoice-v3-flash"
    assert p["parameters"] == {"text_type": "PlainText", "voice": "longxiaochun_v3",
                               "format": "pcm", "sample_rate": 22050}
    assert p["input"] == {}


def test_cosyvoice_continue_and_finish_frames():
    c = _cosyvoice_continue("tid1", "你好")
    assert c["header"]["action"] == "continue-task"
    assert c["payload"]["input"]["text"] == "你好"
    fin = _cosyvoice_finish("tid1")
    assert fin["header"]["action"] == "finish-task"
    assert fin["payload"]["input"] == {}


def test_qwen_session_update_frame():
    s = _qwen_session_update("Cherry", 24000)
    assert s["type"] == "session.update"
    assert s["session"] == {"voice": "Cherry", "response_format": "pcm",
                            "sample_rate": 24000, "mode": "server_commit"}


# ── Mock provider：meta 先出，分片随文字数产出 ──────────────────────────────

@pytest.mark.asyncio
async def test_mock_stream_yields_meta_then_chunks():
    prov = MockStreamingTTSProvider(sample_rate=24000)
    out = [x async for x in prov.stream(_aiter(["你好", "世界"]), voice="", sample_rate=0)]
    assert isinstance(out[0], dict) and out[0]["type"] == "meta"
    assert out[0]["sample_rate"] == 24000 and out[0]["format"] == "pcm"
    audio = [x for x in out if isinstance(x, (bytes, bytearray))]
    assert len(audio) == 2  # 两个文本分片 → 两块音频
    assert all(len(b) > 0 for b in audio)


@pytest.mark.asyncio
async def test_mock_stream_respects_requested_sample_rate():
    prov = MockStreamingTTSProvider(sample_rate=24000)
    out = [x async for x in prov.stream(_aiter(["a"]), voice="", sample_rate=16000)]
    assert out[0]["sample_rate"] == 16000


@pytest.mark.asyncio
async def test_mock_stream_empty_text_only_meta():
    prov = MockStreamingTTSProvider()
    out = [x async for x in prov.stream(_aiter([]), sample_rate=0)]
    assert len(out) == 1 and out[0]["type"] == "meta"


# ── 工厂路由 ────────────────────────────────────────────────────────────────

def test_factory_off_returns_none(monkeypatch):
    monkeypatch.setenv("TTS_STREAM_PROVIDER", "off")
    assert build_tts_stream_provider() is None
    assert build_tts_stream_provider("none") is None


def test_factory_mock(monkeypatch):
    monkeypatch.delenv("TTS_STREAM_PROVIDER", raising=False)
    assert isinstance(build_tts_stream_provider("mock"), MockStreamingTTSProvider)


def test_factory_cosyvoice_needs_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_ASR_KEY", raising=False)
    monkeypatch.delenv("LLM_EMBED_API_KEY", raising=False)
    assert build_tts_stream_provider("cosyvoice") is None  # 无 key → None（回退批处理）


def test_factory_cosyvoice_with_key(monkeypatch):
    monkeypatch.setenv("LLM_EMBED_API_KEY", "sk-test")
    monkeypatch.delenv("TTS_STREAM_MODEL", raising=False)
    prov = build_tts_stream_provider("cosyvoice")
    assert isinstance(prov, DashScopeCosyVoiceProvider)
    assert prov.model == "cosyvoice-v3-flash" and prov.voice == "longxiaochun_v3"
    assert prov.sample_rate == 22050


def test_factory_qwen_with_key_and_voice_override(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_ASR_KEY", "sk-test")
    monkeypatch.delenv("TTS_STREAM_MODEL", raising=False)
    prov = build_tts_stream_provider("qwen", voice="Sunny")
    assert isinstance(prov, DashScopeQwenTTSProvider)
    assert prov.model == "qwen3-tts-flash-realtime" and prov.voice == "Sunny"
    assert prov.sample_rate == 24000


def test_factory_dashscope_alias_maps_to_cosyvoice(monkeypatch):
    monkeypatch.setenv("LLM_EMBED_API_KEY", "sk-test")
    prov = build_tts_stream_provider("dashscope")
    assert isinstance(prov, DashScopeCosyVoiceProvider)


def test_catalog_shape():
    assert set(TTS_STREAM_CATALOG) == {"cosyvoice", "qwen"}
    for cat in TTS_STREAM_CATALOG.values():
        assert cat["model"] and cat["voice"] and cat["sample_rate"] > 0
        assert cat["voices"] and all("voice_id" in v for v in cat["voices"])


# ── FakeWS 驱动 DashScope provider 全循环（离线验证协议状态机）────────────────

class _FakeMsg(types.SimpleNamespace):
    pass


class _FakeWS:
    """脚本化 aiohttp WS：receive() 顺序回放 scripted 消息；send_json 记录发出的帧。"""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.sent = []
        self.closed = False

    async def send_json(self, obj):
        self.sent.append(obj)

    async def send_bytes(self, b):
        self.sent.append(b)

    async def receive(self, timeout=None):
        await asyncio.sleep(0)  # 让 pump 任务有机会发帧
        if self._scripted:
            return self._scripted.pop(0)
        import aiohttp
        return _FakeMsg(type=aiohttp.WSMsgType.CLOSED, data=None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.closed = True
        return False


class _FakeSession:
    def __init__(self, ws):
        self._ws = ws

    def ws_connect(self, *a, **k):
        return self._ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _text(obj):
    import aiohttp
    return _FakeMsg(type=aiohttp.WSMsgType.TEXT, data=json.dumps(obj))


def _binary(b):
    import aiohttp
    return _FakeMsg(type=aiohttp.WSMsgType.BINARY, data=b)


@pytest.mark.asyncio
async def test_cosyvoice_provider_full_loop(monkeypatch):
    import aiohttp
    ws = _FakeWS([
        _text({"header": {"event": "task-started"}}),
        _binary(b"\x11\x22" * 100),
        _binary(b"\x33\x44" * 100),
        _text({"header": {"event": "task-finished"}}),
    ])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(ws))
    prov = DashScopeCosyVoiceProvider("sk", "wss://x/inference", "cosyvoice-v3-flash",
                                      voice="longxiaochun_v3", sample_rate=22050)
    out = [x async for x in prov.stream(_aiter(["杭州", "天气"]), voice="", sample_rate=0)]
    # 首个非二进制 yield 是 meta
    metas = [x for x in out if isinstance(x, dict)]
    audio = [x for x in out if isinstance(x, (bytes, bytearray))]
    assert metas and metas[0]["type"] == "meta" and metas[0]["sample_rate"] == 22050
    assert len(audio) == 2 and b"".join(audio) == b"\x11\x22" * 100 + b"\x33\x44" * 100
    # 发出帧含 run-task + continue-task×2 + finish-task
    actions = [f["header"]["action"] for f in ws.sent if isinstance(f, dict) and "header" in f]
    assert actions[0] == "run-task"
    assert actions.count("continue-task") == 2
    assert "finish-task" in actions


@pytest.mark.asyncio
async def test_cosyvoice_provider_task_failed_raises(monkeypatch):
    import aiohttp
    ws = _FakeWS([
        _text({"header": {"event": "task-started"}}),
        _text({"header": {"event": "task-failed", "error_message": "Engine 418"}}),
    ])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(ws))
    prov = DashScopeCosyVoiceProvider("sk", "wss://x/inference", "cosyvoice-v3-flash")
    with pytest.raises(RuntimeError, match="418"):
        _ = [x async for x in prov.stream(_aiter(["x"]))]


@pytest.mark.asyncio
async def test_qwen_provider_full_loop(monkeypatch):
    import aiohttp
    pcm = b"\x55\x66" * 50
    ws = _FakeWS([
        _text({"type": "session.created"}),
        _text({"type": "session.updated"}),
        _text({"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()}),
        _text({"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()}),
        _text({"type": "response.done"}),
    ])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(ws))
    prov = DashScopeQwenTTSProvider("sk", "wss://x/realtime", "qwen3-tts-flash-realtime",
                                    voice="Cherry", sample_rate=24000)
    out = [x async for x in prov.stream(_aiter(["你好"]), voice="", sample_rate=0)]
    metas = [x for x in out if isinstance(x, dict)]
    audio = [x for x in out if isinstance(x, (bytes, bytearray))]
    assert metas[0]["type"] == "meta" and metas[0]["sample_rate"] == 24000
    assert b"".join(audio) == pcm * 2
    types_sent = [f.get("type") for f in ws.sent if isinstance(f, dict)]
    assert "session.update" in types_sent
    assert "input_text_buffer.append" in types_sent
    assert "input_text_buffer.commit" in types_sent
    assert "session.finish" in types_sent
