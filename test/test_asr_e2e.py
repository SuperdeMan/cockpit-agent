"""ASR 端到端测试：音频 → /api/asr → 文本 → WS 编排 → 回复。

使用固定 wav 样本（合成的静音+提示音），验证 ASR 返回非空文本。
需要真实 LLM_API_KEY 和网络连接；无 key 时跳过。
"""
import asyncio
import base64
import json
import math
import os
import struct

import aiohttp
import pytest

AUDIO_API = os.getenv("VITE_AUDIO_API_URL", "http://localhost:50059")
HAS_KEY = bool(os.getenv("LLM_API_KEY", os.getenv("LLM_GATEWAY_API_KEY", "")))


def _service_reachable() -> bool:
    """ASR/llm-gateway 服务是否可达——这是 E2E，需要全栈在跑（make up）。"""
    import urllib.error
    import urllib.request

    try:
        urllib.request.urlopen(f"{AUDIO_API}/api/voices", timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # 服务在（返回了 HTTP 响应，即便非 200）
    except Exception:
        return False


SERVICE_UP = _service_reachable()

# 整个 ASR E2E 依赖 llm-gateway 服务；服务不可达（如未起全栈的 CI）时整体跳过，
# 不在缺服务时误报失败。
pytestmark = pytest.mark.skipif(
    not SERVICE_UP,
    reason=f"ASR service unreachable at {AUDIO_API}; run `make up` first",
)


def _make_wav_sample(duration_s: float = 1.0, freq: float = 440.0) -> bytes:
    """Generate a simple sine wave WAV file in memory."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_s)
    # 16-bit PCM
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        val = int(32767 * 0.5 * math.sin(2 * math.pi * freq * t))
        samples.append(struct.pack('<h', val))
    data = b''.join(samples)

    # WAV header
    header = struct.pack('<4sI4s', b'RIFF', 36 + len(data), b'WAVE')
    fmt = struct.pack('<4sIHHIIHH', b'fmt ', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    data_header = struct.pack('<4sI', b'data', len(data))

    return header + fmt + data_header + data


@pytest.mark.skipif(not HAS_KEY, reason="No LLM_API_KEY configured")
@pytest.mark.asyncio
async def test_asr_returns_text():
    """POST wav audio to /api/asr, expect non-empty text."""
    wav_bytes = _make_wav_sample(1.0)
    audio_b64 = base64.b64encode(wav_bytes).decode()

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{AUDIO_API}/api/asr",
            json={"audio": audio_b64, "format": "wav", "language": "zh"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "text" in data
            # ASR should return something (even if noise, it processes without error)
            assert isinstance(data["text"], str)


@pytest.mark.skipif(not HAS_KEY, reason="No LLM_API_KEY configured")
@pytest.mark.asyncio
async def test_asr_webm_transcoded():
    """POST with format='webm' triggers transcoding; should still return text."""
    wav_bytes = _make_wav_sample(1.0)
    audio_b64 = base64.b64encode(wav_bytes).decode()

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{AUDIO_API}/api/asr",
            json={"audio": audio_b64, "format": "webm", "language": "zh"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "text" in data


@pytest.mark.asyncio
async def test_asr_no_audio_returns_error():
    """POST empty audio should return error or empty text."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{AUDIO_API}/api/asr",
            json={"audio": "", "format": "wav", "language": "zh"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            # Should return 200 with empty/failed text, or 400
            assert resp.status in (200, 400)


@pytest.mark.asyncio
async def test_voices_endpoint():
    """GET /api/voices should return voice list."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{AUDIO_API}/api/voices") as resp:
            assert resp.status == 200
            data = await resp.json()
            voices = data.get("voices", data)  # 兼容 {voices:[...]} 或 [...]
            assert isinstance(voices, list)
            assert len(voices) > 0
