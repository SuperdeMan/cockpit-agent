"""Tests for webm -> wav transcoding in the ASR pipeline."""
from __future__ import annotations

import asyncio
import pytest

# Ensure the llm-gateway package is importable when running from repo root.
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from http_server import _transcode_to_wav, _WAV_FORMATS


# ── passthrough for compatible formats ──────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", sorted(_WAV_FORMATS))
async def test_wav_pcm_passthrough(fmt: str):
    """wav / pcm / pcm16 input must be returned byte-identical."""
    data = b"RIFF\x00\x00fake-wav-data"
    result = await _transcode_to_wav(data, fmt)
    assert result is data  # same object, not just equal


# ── successful transcode (mock ffmpeg) ──────────────────────────────────────

class _FakeProc:
    """Minimal stand-in for asyncio.subprocess.Process."""
    def __init__(self, stdout: bytes, returncode: int = 0):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self, input=None):
        return self._stdout, b""


@pytest.mark.asyncio
async def test_transcode_invokes_ffmpeg(monkeypatch):
    """Non-wav input should trigger ffmpeg and return transcoded output."""
    fake_wav = b"RIFF\x00\x00converted-wav"
    original = b"\x1aE\xdf\xa3fake-webm-data"

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(fake_wav)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await _transcode_to_wav(original, "webm")
    assert result == fake_wav


@pytest.mark.asyncio
async def test_transcode_passes_correct_ffmpeg_args(monkeypatch):
    """Verify the ffmpeg command includes -ar 16000 -ac 1 -f wav."""
    captured_args = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args["args"] = args
        captured_args["kwargs"] = kwargs
        return _FakeProc(b"RIFFconverted")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await _transcode_to_wav(b"fake-ogg", "ogg")

    assert captured_args["args"] == (
        "ffmpeg", "-i", "pipe:0",
        "-ar", "16000", "-ac", "1", "-f", "wav",
        "pipe:1",
    )
    assert captured_args["kwargs"]["stdin"] == asyncio.subprocess.PIPE
    assert captured_args["kwargs"]["stdout"] == asyncio.subprocess.PIPE


# ── fallback when ffmpeg fails ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_on_nonzero_exit(monkeypatch):
    """If ffmpeg returns non-zero, original bytes are returned."""
    original = b"\x1aE\xdf\xa3bad-input"

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(b"", returncode=1)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await _transcode_to_wav(original, "webm")
    assert result is original


@pytest.mark.asyncio
async def test_fallback_on_empty_stdout(monkeypatch):
    """If ffmpeg returns 0 but empty stdout, original bytes are returned."""
    original = b"\x1aE\xdf\xa3empty-output"

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc(b"", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await _transcode_to_wav(original, "webm")
    assert result is original


# ── fallback when ffmpeg is not installed ────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_when_ffmpeg_missing(monkeypatch):
    """If ffmpeg is not installed (FileNotFoundError), original bytes are returned."""
    original = b"\x1aE\xdf\xa3no-ffmpeg"

    async def fake_create_subprocess_exec(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await _transcode_to_wav(original, "webm")
    assert result is original
