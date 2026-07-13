"""批处理 ASR/TTS 工厂 + 流式桥接 单测——全部离线可跑。

背景：批处理面（/api/asr、/api/tts + gRPC Transcribe/Synthesize）此前硬绑 MiMo，
chat 换家（LLM_PROVIDER≠mimo 系）即静默降级 Mock。本组用例固化新契约：
ASR_PROVIDER/TTS_PROVIDER 显式可配 + auto 下桥接流式引擎，不用 MiMo 也有真 ASR/TTS。
"""
from __future__ import annotations

import asyncio

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import providers as P
from providers import (
    _wav_header, _wav_pcm_data,
    build_asr_provider, build_tts_provider,
    MiMoASRProvider, MiMoTTSProvider, MockASRProvider, MockTTSProvider,
    StreamBridgeASRProvider, StreamBridgeTTSProvider,
)

_AUDIO_ENVS = (
    "ASR_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER", "LLM_API_KEY",
    "DASHSCOPE_ASR_KEY", "LLM_EMBED_API_KEY", "MINIMAX_API_KEY",
    "TTS_STREAM_PROVIDER", "MIMO_AUDIO_BASE_URL",
)


def _clean_env(monkeypatch):
    for k in _AUDIO_ENVS:
        monkeypatch.delenv(k, raising=False)


# ── 工厂：ASR ──────────────────────────────────────────────────────────

def test_asr_default_mimo_unchanged(monkeypatch):
    """历史现状不变：LLM_PROVIDER=mimo 系 + 有 key → MiMo 批处理。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "xiaomimimo")
    monkeypatch.setenv("LLM_API_KEY", "mk")
    assert isinstance(build_asr_provider(), MiMoASRProvider)


def test_asr_chat_switched_bridges_dashscope(monkeypatch):
    """chat 换家 + 有百炼 key → 桥接 dashscope 流式引擎（此前会静默 Mock）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "dsk")
    monkeypatch.setenv("LLM_EMBED_API_KEY", "bailian")
    prov = build_asr_provider()
    assert isinstance(prov, StreamBridgeASRProvider) and prov.provider == "dashscope"


def test_asr_chat_switched_no_key_mock(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "dsk")
    assert isinstance(build_asr_provider(), MockASRProvider)


def test_asr_explicit_mimo_pins_despite_chat_switch(monkeypatch):
    """显式 ASR_PROVIDER=mimo：chat 切走后批处理仍钉住 MiMo。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "mk")
    monkeypatch.setenv("ASR_PROVIDER", "mimo")
    assert isinstance(build_asr_provider(), MiMoASRProvider)


def test_asr_explicit_mock_wins(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "xiaomimimo")
    monkeypatch.setenv("LLM_API_KEY", "mk")
    monkeypatch.setenv("ASR_PROVIDER", "mock")
    assert isinstance(build_asr_provider(), MockASRProvider)


# ── 工厂：TTS ──────────────────────────────────────────────────────────

def test_tts_default_mimo_unchanged(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "xiaomimimo")
    monkeypatch.setenv("LLM_API_KEY", "mk")
    assert isinstance(build_tts_provider(), MiMoTTSProvider)


def test_tts_chat_switched_bridges_stream_engine(monkeypatch):
    """chat 换家 → 批处理 TTS 跟随 TTS_STREAM_PROVIDER（默认 cosyvoice）桥接。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "dsk")
    monkeypatch.setenv("LLM_EMBED_API_KEY", "bailian")
    prov = build_tts_provider()
    assert isinstance(prov, StreamBridgeTTSProvider) and prov.engine == "cosyvoice"


def test_tts_explicit_minimax(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TTS_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "mmk")
    prov = build_tts_provider()
    assert isinstance(prov, StreamBridgeTTSProvider) and prov.engine == "minimax"


def test_tts_explicit_engine_without_key_mock(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("TTS_PROVIDER", "minimax")
    assert isinstance(build_tts_provider(), MockTTSProvider)


def test_tts_explicit_mimo_pins(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_API_KEY", "mk")
    monkeypatch.setenv("TTS_PROVIDER", "mimo")
    assert isinstance(build_tts_provider(), MiMoTTSProvider)


# ── MiMo 端点可配 ──────────────────────────────────────────────────────

def test_mimo_audio_base_url_override(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("MIMO_AUDIO_BASE_URL", "https://alt.example.com/v1/chat/completions")
    assert MiMoASRProvider("k").base_url == "https://alt.example.com/v1/chat/completions"
    assert MiMoTTSProvider("k").base_url == "https://alt.example.com/v1/chat/completions"
    # 缺省回落官方集群
    monkeypatch.delenv("MIMO_AUDIO_BASE_URL", raising=False)
    assert MiMoTTSProvider("k").base_url == MiMoTTSProvider.BASE_URL


# ── WAV data 块提取 ────────────────────────────────────────────────────

def test_wav_pcm_data_standard_and_raw():
    pcm = b"\x01\x02" * 100
    assert _wav_pcm_data(_wav_header(len(pcm)) + pcm) == pcm
    assert _wav_pcm_data(pcm) == pcm  # 非 RIFF 视为裸 PCM


def test_wav_pcm_data_streaming_placeholder_size():
    """ffmpeg pipe 产物：data size 可能是 0xFFFFFFFF 占位 → 取到末尾。"""
    pcm = b"\xaa\xbb" * 50
    hdr = bytearray(_wav_header(len(pcm)))
    hdr[-4:] = (0xFFFFFFFF).to_bytes(4, "little")
    assert _wav_pcm_data(bytes(hdr) + pcm) == pcm


# ── 流式桥接：TTS ──────────────────────────────────────────────────────

class _FakeStreamTTS:
    def __init__(self, sr=22050):
        self.sr = sr
        self.seen_voice = None

    async def stream(self, text_deltas, *, voice="", sample_rate=0):
        self.seen_voice = voice
        async for _ in text_deltas:
            pass
        yield {"type": "meta", "sample_rate": self.sr, "format": "pcm"}
        yield b"\x00\x01" * 800
        yield b"\x02\x03" * 800


def test_bridge_tts_synthesize_wav(monkeypatch):
    fake = _FakeStreamTTS(sr=22050)
    monkeypatch.setattr(P, "build_tts_stream_provider", lambda *a, **k: fake)
    bridge = StreamBridgeTTSProvider("cosyvoice")
    audio, fmt, dur, model, voice = asyncio.run(
        bridge.synthesize("你好世界", voice_id="冰糖", model="mimo-v2.5-tts",
                          speed=1.0, fmt="wav"))
    assert fmt == "wav" and audio[:4] == b"RIFF"
    assert _wav_pcm_data(audio) == b"\x00\x01" * 800 + b"\x02\x03" * 800
    # MiMo 音色「冰糖」不属于 cosyvoice → 不透传（引擎用自己默认），避免跨引擎 4xx
    assert fake.seen_voice == ""
    assert model == "cosyvoice-v3-flash"
    assert voice == "longxiaochun_v3"
    assert dur == int(3200 / (22050 * 2) * 1000)


def test_bridge_tts_known_voice_passthrough(monkeypatch):
    fake = _FakeStreamTTS()
    monkeypatch.setattr(P, "build_tts_stream_provider", lambda *a, **k: fake)
    bridge = StreamBridgeTTSProvider("cosyvoice")
    asyncio.run(bridge.synthesize("hi", voice_id="longze_v3", model="",
                                  speed=1.0, fmt="pcm16"))
    assert fake.seen_voice == "longze_v3"


def test_bridge_tts_list_voices():
    bridge = StreamBridgeTTSProvider("qwen")
    voices = asyncio.run(bridge.list_voices(language="zh", gender="male"))
    assert voices and all(v["gender"] == "male" for v in voices)


# ── 流式桥接：ASR ──────────────────────────────────────────────────────

class _FakeStreamASR:
    model = "fake-rt-model"

    def __init__(self):
        self.frames = []

    async def stream(self, pcm_chunks, *, language="zh"):
        async for c in pcm_chunks:
            self.frames.append(c)
        yield {"text": "打开", "final": False}
        yield {"text": "打开空调", "final": True}


def test_bridge_asr_transcribe(monkeypatch):
    fake = _FakeStreamASR()
    monkeypatch.setattr(P, "build_streaming_asr_provider", lambda *a, **k: fake)
    bridge = StreamBridgeASRProvider("dashscope")
    pcm = b"\x00\x01" * 16000  # 1s @16k s16le
    wav = _wav_header(len(pcm)) + pcm
    text, conf, lang, model, dur = asyncio.run(
        bridge.transcribe(audio=wav, fmt="wav", language="zh", model="mimo-v2.5-asr"))
    assert text == "打开空调"
    assert model == "fake-rt-model"
    assert dur == 1000
    assert b"".join(fake.frames) == pcm  # WAV 头被剥掉、裸 PCM 完整喂入


def test_bridge_asr_no_engine_raises(monkeypatch):
    monkeypatch.setattr(P, "build_streaming_asr_provider", lambda *a, **k: None)
    bridge = StreamBridgeASRProvider("dashscope")
    try:
        asyncio.run(bridge.transcribe(audio=b"", fmt="wav", language="zh", model=""))
        assert False, "should raise"
    except RuntimeError:
        pass


# ── chat 4xx 响应体可诊断（badcase 6d29929e：MiniMax 422 只留状态码无从判因）────

class _Fake4xxResp:
    status_code = 422
    text = '{"base_resp":{"status_code":2013,"status_msg":"invalid params: messages"}}'

    def json(self):
        return {}


class _Fake4xxClient:
    async def post(self, url, headers=None, json=None, timeout=None):
        return _Fake4xxResp()


def test_complete_4xx_error_carries_response_body(monkeypatch):
    from providers import OpenAICompatibleProvider
    prov = OpenAICompatibleProvider("k")
    monkeypatch.setattr(prov, "_get_client", lambda: _Fake4xxClient())
    try:
        asyncio.run(prov.complete(
            [{"role": "user", "content": "hi"}], "m", 0.2, 100))
        assert False, "should raise"
    except RuntimeError as e:
        msg = str(e)
        assert "HTTP 422" in msg and "invalid params" in msg
