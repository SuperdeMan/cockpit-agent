from __future__ import annotations

import base64
import io
import json
import threading
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import scripts.prepare_voiceprint_fixtures as fixtures
from scripts.prepare_voiceprint_fixtures import (
    FixtureError,
    _wav_to_pcm16_mono,
    prepare_fixtures,
    verify_fixtures,
)


def _wav_bytes(
    *,
    frames: int = 320,
    sample_rate: int = 16_000,
    channels: int = 1,
    sample_width: int = 2,
    sample: int = 1,
) -> bytes:
    payload = sample.to_bytes(
        sample_width,
        "little",
        signed=sample_width > 1,
    ) * frames * channels
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(payload)
    return stream.getvalue()


@contextmanager
def _audio_api(
    *,
    voices: list[dict] | None = None,
    audio: bytes | None = None,
    response_format: str = "wav",
    provider_by_call: dict[int, str] | None = None,
    model_by_call: dict[int, str] | None = None,
    stream_provider: str | None = None,
):
    calls: list[tuple[str, dict | None]] = []
    catalog = voices or [
        {
            "voice_id": "voice-f",
            "language": "zh",
            "gender": "female",
        },
        {
            "voice_id": "voice-f2",
            "language": "zh",
            "gender": "female",
        },
        {
            "voice_id": "voice-m",
            "language": "zh",
            "gender": "male",
        },
    ]
    default_audio = {
        voice["voice_id"]: _wav_bytes(sample=index + 1)
        for index, voice in enumerate(catalog)
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            calls.append((self.path, None))
            if self.path == "/api/tts/stream/info" and stream_provider:
                body = json.dumps({
                    "streaming": True,
                    "default": stream_provider,
                    "providers": [{
                        "id": stream_provider,
                        "available": True,
                        "model": f"{stream_provider}-model",
                        "voices": catalog,
                    }],
                }).encode()
            elif self.path in {
                "/api/voices",
                f"/api/voices?provider={stream_provider}",
            }:
                body = json.dumps({"voices": catalog}).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            calls.append((self.path, request))
            if self.path != "/api/tts":
                self.send_error(404)
                return
            call_number = sum(path == "/api/tts" for path, _body in calls)
            wav = (
                audio
                if audio is not None
                else default_audio[request["voice_id"]]
            )
            body = json.dumps({
                "audio": base64.b64encode(wav).decode("ascii"),
                "format": response_format,
                "provider": (provider_by_call or {}).get(
                    call_number,
                    request.get("provider", "fixture-provider"),
                ),
                "model": (model_by_call or {}).get(
                    call_number,
                    (
                        f"{request['provider']}-model"
                        if request.get("provider")
                        else "fixture-model-v1"
                    ),
                ),
                "voice_id": request["voice_id"],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _manifest(path: Path) -> dict:
    return json.loads(
        (path / "voiceprint-fixtures.json").read_text(encoding="utf-8"),
    )


def test_prepare_generates_eight_verified_pcm_files_from_distinct_gender_voices(
    tmp_path: Path,
):
    artifact_dir = tmp_path / "run-owned"
    with _audio_api() as (base_url, calls):
        manifest_path = prepare_fixtures(
            artifact_dir,
            audio_api_url=base_url,
        )

    assert manifest_path == artifact_dir / "voiceprint-fixtures.json"
    manifest = _manifest(artifact_dir)
    assert manifest["schema_version"] == 1
    assert manifest["provider"] == "fixture-provider"
    assert manifest["model"] == "fixture-model-v1"
    assert [(voice["slot"], voice["voice_id"], voice["gender"])
            for voice in manifest["voices"]] == [
        ("A", "voice-f", "female"),
        ("B", "voice-m", "male"),
    ]
    assert manifest["audio"] == {
        "sample_rate_hz": 16_000,
        "channels": 1,
        "bit_depth": 16,
        "encoding": "s16le",
    }
    assert manifest["synthetic_functional_only"] is True
    assert manifest["no_human_biometric"] is True
    assert len(manifest["texts"]["enroll"]) == 3
    assert isinstance(manifest["texts"]["probe"], str)
    assert len(manifest["files"]) == 8
    assert {
        (item["speaker"], item["purpose"], item["text_key"])
        for item in manifest["files"]
    } == {
        (speaker, "enroll", f"enroll_{number}")
        for speaker in ("A", "B")
        for number in range(1, 4)
    } | {
        ("A", "probe", "probe"),
        ("B", "probe", "probe"),
    }
    for item in manifest["files"]:
        payload = (artifact_dir / item["path"]).read_bytes()
        assert payload
        assert len(payload) % 2 == 0
        assert item["bytes"] == len(payload)
        assert len(item["sha256"]) == 64
    assert [path for path, _ in calls].count("/api/voices") == 1
    assert [path for path, _ in calls].count("/api/tts") == 8
    assert verify_fixtures(artifact_dir) == manifest_path
    assert "credential" not in json.dumps(manifest).lower()
    assert "token" not in json.dumps(manifest).lower()


def test_prepare_pins_the_advertised_real_stream_provider_for_batch_calls(
    tmp_path: Path,
):
    artifact_dir = tmp_path / "run-owned"
    with _audio_api(stream_provider="cosyvoice") as (base_url, calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)

    manifest = _manifest(artifact_dir)
    assert manifest["provider"] == "cosyvoice"
    assert manifest["model"] == "cosyvoice-model"
    assert ("/api/tts/stream/info", None) in calls
    assert ("/api/voices?provider=cosyvoice", None) in calls
    tts_bodies = [body for path, body in calls if path == "/api/tts"]
    assert len(tts_bodies) == 8
    assert all(body["provider"] == "cosyvoice" for body in tts_bodies)


@pytest.mark.parametrize(
    ("api_kwargs", "message"),
    [
        ({"provider_by_call": {8: "other-provider"}}, "provider"),
        ({"model_by_call": {8: "other-model"}}, "model"),
    ],
)
def test_prepare_requires_one_provider_and_model_across_all_eight_calls(
    tmp_path: Path,
    api_kwargs: dict,
    message: str,
):
    with _audio_api(**api_kwargs) as (base_url, calls):
        with pytest.raises(FixtureError, match=message):
            prepare_fixtures(tmp_path / "owned", audio_api_url=base_url)

    assert [path for path, _ in calls].count("/api/tts") == 8


def test_prepare_rejects_identical_normalized_audio_for_two_voices(
    tmp_path: Path,
):
    with _audio_api(audio=_wav_bytes()) as (base_url, calls):
        with pytest.raises(FixtureError, match="identical"):
            prepare_fixtures(tmp_path / "owned", audio_api_url=base_url)

    assert [path for path, _ in calls].count("/api/tts") >= 5


def test_normalizes_48khz_stereo_to_exact_16khz_mono_pcm():
    frames = b"".join(
        (1000).to_bytes(2, "little", signed=True)
        + (3000).to_bytes(2, "little", signed=True)
        for _ in range(480)
    )
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48_000)
        output.writeframes(frames)

    pcm = _wav_to_pcm16_mono(stream.getvalue())

    assert len(pcm) == 160 * 2
    assert int.from_bytes(pcm[:2], "little", signed=True) == 2000


def test_rejects_one_hertz_wav_before_resample_amplification():
    with pytest.raises(FixtureError, match="sample rate"):
        _wav_to_pcm16_mono(_wav_bytes(frames=2, sample_rate=1))


def test_rejects_truncated_stereo_frame_payload():
    truncated = _wav_bytes(frames=320, channels=2)[:-2]

    with pytest.raises(FixtureError, match="truncated|frame"):
        _wav_to_pcm16_mono(truncated)


def test_rejects_wav_over_duration_limit():
    with pytest.raises(FixtureError, match="duration"):
        _wav_to_pcm16_mono(
            _wav_bytes(frames=16_000 * 31, sample_rate=16_000),
        )


def test_rejects_wav_whose_normalized_output_exceeds_limit():
    with pytest.raises(FixtureError, match="output"):
        _wav_to_pcm16_mono(
            _wav_bytes(frames=8_000 * 25, sample_rate=8_000),
        )


def test_normalization_resource_failures_are_fixture_errors(monkeypatch):
    def fail_array(*_args, **_kwargs):
        raise MemoryError("simulated allocation failure")

    monkeypatch.setattr(fixtures.array, "array", fail_array)

    with pytest.raises(FixtureError, match="resource"):
        _wav_to_pcm16_mono(_wav_bytes())


def test_prepare_rejects_fewer_than_two_distinct_voices(tmp_path: Path):
    voices = [
        {"voice_id": "only-one", "language": "zh", "gender": "female"},
        {"voice_id": "only-one", "language": "zh", "gender": "female"},
    ]
    with _audio_api(voices=voices) as (base_url, _calls):
        with pytest.raises(FixtureError, match="two distinct"):
            prepare_fixtures(tmp_path / "owned", audio_api_url=base_url)


@pytest.mark.parametrize(
    ("voices", "expected"),
    [
        (
            [
                {"voice_id": "neutral-a", "language": "zh", "gender": "neutral"},
                {"voice_id": "neutral-b", "language": "zh", "gender": "neutral"},
            ],
            ("neutral-a", "neutral-b"),
        ),
        (
            [
                {"voice_id": "unspecified-a", "language": "zh"},
                {"voice_id": "unspecified-b", "language": "zh"},
            ],
            ("unspecified-a", "unspecified-b"),
        ),
    ],
)
def test_prepare_accepts_distinct_nonalias_voices_without_binary_gender(
    tmp_path: Path,
    voices: list[dict],
    expected: tuple[str, str],
):
    artifact_dir = tmp_path / "owned"
    with _audio_api(voices=voices) as (base_url, _calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)

    manifest = _manifest(artifact_dir)
    assert tuple(voice["voice_id"] for voice in manifest["voices"]) == expected


@pytest.mark.parametrize(
    ("audio", "response_format", "message"),
    [
        (b"", "wav", "empty"),
        (b"not-a-wave", "wav", "WAV"),
        (_wav_bytes(), "mp3", "format"),
        (_wav_bytes(sample_width=1), "wav", "16-bit"),
    ],
)
def test_prepare_rejects_empty_or_invalid_audio(
    tmp_path: Path,
    audio: bytes,
    response_format: str,
    message: str,
):
    with _audio_api(audio=audio, response_format=response_format) as (
        base_url,
        _calls,
    ):
        with pytest.raises(FixtureError, match=message):
            prepare_fixtures(tmp_path / "owned", audio_api_url=base_url)


def test_prepare_requires_an_exclusive_empty_artifact_directory(tmp_path: Path):
    artifact_dir = tmp_path / "already-used"
    artifact_dir.mkdir()
    (artifact_dir / "old.txt").write_text("old", encoding="utf-8")
    with _audio_api() as (base_url, _calls):
        with pytest.raises(FixtureError, match="empty"):
            prepare_fixtures(artifact_dir, audio_api_url=base_url)


def test_verify_rejects_hash_mismatch_and_extra_or_missing_files(tmp_path: Path):
    artifact_dir = tmp_path / "owned"
    with _audio_api() as (base_url, _calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)
    manifest = _manifest(artifact_dir)
    first = artifact_dir / manifest["files"][0]["path"]
    original = first.read_bytes()

    first.write_bytes(original + b"\x00\x00")
    with pytest.raises(FixtureError, match="hash|bytes"):
        verify_fixtures(artifact_dir)
    first.write_bytes(original)

    extra = artifact_dir / "extra.pcm"
    extra.write_bytes(b"\x00\x00")
    with pytest.raises(FixtureError, match="extra"):
        verify_fixtures(artifact_dir)
    extra.unlink()

    first.unlink()
    with pytest.raises(FixtureError, match="missing"):
        verify_fixtures(artifact_dir)


def test_verify_streams_pcm_hash_without_reading_entire_audio(
    tmp_path: Path,
    monkeypatch,
):
    artifact_dir = tmp_path / "owned"
    with _audio_api() as (base_url, _calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)
    original = Path.read_bytes

    def reject_pcm_read_bytes(path: Path):
        if path.suffix == ".pcm":
            raise AssertionError("PCM verification must stream")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_pcm_read_bytes)

    assert verify_fixtures(artifact_dir).is_file()


def test_verify_stats_and_rejects_pcm_over_limit_before_hashing(
    tmp_path: Path,
    monkeypatch,
):
    artifact_dir = tmp_path / "owned"
    with _audio_api() as (base_url, _calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)
    monkeypatch.setattr(fixtures, "MAX_NORMALIZED_PCM_BYTES", 1, raising=False)

    with pytest.raises(FixtureError, match="oversized"):
        verify_fixtures(artifact_dir)


def test_manifest_is_statted_and_bounded_before_read(
    tmp_path: Path,
    monkeypatch,
):
    artifact_dir = tmp_path / "owned"
    artifact_dir.mkdir()
    manifest = artifact_dir / "voiceprint-fixtures.json"
    manifest.write_bytes(b"{" + b"x" * fixtures._MAX_JSON_BYTES)
    original = Path.read_bytes

    def reject_manifest_read_bytes(path: Path):
        if path == manifest:
            raise AssertionError("manifest must use a bounded stream read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_manifest_read_bytes)

    with pytest.raises(FixtureError, match="oversized"):
        verify_fixtures(artifact_dir)


def test_pcm_exact_bounded_read_rejects_post_verify_mutation(tmp_path: Path):
    artifact_dir = tmp_path / "owned"
    with _audio_api() as (base_url, _calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)
    verify_fixtures(artifact_dir)
    manifest = _manifest(artifact_dir)
    item = manifest["files"][0]
    pcm_path = artifact_dir / item["path"]
    original = pcm_path.read_bytes()
    pcm_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])

    with pytest.raises(FixtureError, match="changed|hash"):
        fixtures.read_verified_pcm(
            pcm_path,
            declared_bytes=item["bytes"],
            declared_sha256=item["sha256"],
        )


def test_manifest_loader_source_never_uses_unbounded_read_bytes():
    import inspect

    source = inspect.getsource(fixtures._read_manifest_bytes)

    assert "read_bytes" not in source
    assert "_MAX_JSON_BYTES + 1" in source


def test_verify_rejects_path_traversal_duplicate_keys_and_bad_schema(
    tmp_path: Path,
):
    artifact_dir = tmp_path / "owned"
    with _audio_api() as (base_url, _calls):
        prepare_fixtures(artifact_dir, audio_api_url=base_url)
    manifest_path = artifact_dir / "voiceprint-fixtures.json"
    valid = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(valid)

    manifest["files"][0]["path"] = "../escape.pcm"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FixtureError, match="path"):
        verify_fixtures(artifact_dir)

    manifest_path.write_text(
        valid.rstrip()[:-1] + ', "provider": "duplicate"}',
        encoding="utf-8",
    )
    with pytest.raises(FixtureError, match="duplicate"):
        verify_fixtures(artifact_dir)

    manifest = json.loads(valid)
    manifest["unexpected"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FixtureError, match="schema|keys"):
        verify_fixtures(artifact_dir)
