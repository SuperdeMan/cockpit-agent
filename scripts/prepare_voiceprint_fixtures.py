#!/usr/bin/env python3
"""Prepare and offline-verify run-scoped synthetic voiceprint fixtures."""

from __future__ import annotations

import argparse
import array
import base64
import hashlib
import io
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


SCHEMA_VERSION = 1
MANIFEST_NAME = "voiceprint-fixtures.json"
SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
BIT_DEPTH = 16
ENCODING = "s16le"
ENROLL_TEXTS = (
    "你好，我是这辆车的常用乘客",
    "今天天气不错，路上应该不太堵",
    "帮我把空调调到二十四度",
)
PROBE_TEXT = "附近有什么好吃的川菜馆推荐"

_TOP_LEVEL_KEYS = frozenset({
    "schema_version",
    "provider",
    "model",
    "voices",
    "texts",
    "audio",
    "files",
    "generated_at",
    "synthetic_functional_only",
    "no_human_biometric",
})
_VOICE_KEYS = frozenset({"slot", "voice_id", "language", "gender"})
_TEXT_KEYS = frozenset({"enroll", "probe"})
_AUDIO_KEYS = frozenset({
    "sample_rate_hz",
    "channels",
    "bit_depth",
    "encoding",
})
_FILE_KEYS = frozenset({
    "speaker",
    "purpose",
    "text_key",
    "path",
    "bytes",
    "sha256",
})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_AUDIO_BYTES = 64 * 1024 * 1024
MIN_WAV_SAMPLE_RATE_HZ = 8_000
MAX_WAV_SAMPLE_RATE_HZ = 192_000
MAX_WAV_DURATION_S = 30
MAX_PCM_INPUT_BYTES = 16 * 1024 * 1024
MAX_NORMALIZED_PCM_BYTES = 640_000
_HASH_CHUNK_BYTES = 64 * 1024


class FixtureError(RuntimeError):
    """The synthetic fixture contract could not be established."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json_bytes(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_JSON_BYTES:
        raise FixtureError(f"{label} JSON is empty or oversized")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"{label} JSON is invalid") from exc
    if type(value) is not dict:
        raise FixtureError(f"{label} JSON must be an object")
    return value


def _request_json(
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read(_MAX_JSON_BYTES + 1)
            if response.status < 200 or response.status >= 300:
                raise FixtureError("audio API returned a non-success status")
    except FixtureError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FixtureError("audio API request failed") from exc
    return _load_json_bytes(raw, label="audio API response")


def _require_exact_keys(
    value: dict[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    if set(value) != expected:
        raise FixtureError(f"{label} schema keys are invalid")


def _voice_catalog(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if set(payload) != {"voices"} or type(payload["voices"]) is not list:
        raise FixtureError("voice catalog schema is invalid")
    voices: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload["voices"]:
        if type(raw) is not dict:
            continue
        voice_id = raw.get("voice_id")
        if not isinstance(voice_id, str) or not voice_id.strip():
            continue
        voice_id = voice_id.strip()
        if voice_id in seen:
            continue
        seen.add(voice_id)
        language = raw.get("language", "")
        gender = raw.get("gender", "")
        tags = raw.get("tags", [])
        alias = (
            raw.get("is_alias") is True
            or "default" in voice_id.lower()
            or (
                type(tags) is list
                and any(
                    isinstance(tag, str)
                    and tag.strip().lower() in {"default", "alias", "默认"}
                    for tag in tags
                )
            )
        )
        voices.append({
            "voice_id": voice_id,
            "language": language if isinstance(language, str) else "",
            "gender": gender.lower() if isinstance(gender, str) else "",
            "alias": alias,
        })
    return voices


def _select_voices(
    catalog: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    usable = [
        voice
        for voice in catalog
        if not voice["alias"]
    ]
    if len(usable) < 2:
        raise FixtureError(
            "at least two distinct non-alias voices are required",
        )
    language_ordered = [
        voice
        for voice in usable
        if voice["language"].lower().startswith("zh")
    ] + [
        voice
        for voice in usable
        if not voice["language"].lower().startswith("zh")
    ]
    for index, first in enumerate(language_ordered):
        for second in language_ordered[index + 1:]:
            if (
                {first["gender"], second["gender"]} == {"female", "male"}
            ):
                return (
                    (first, second)
                    if first["gender"] == "female"
                    else (second, first)
                )
    return language_ordered[0], language_ordered[1]


def _discover_stream_provider(
    base_url: str,
    *,
    timeout_s: float,
) -> str:
    """Prefer a runtime-advertised real engine with two usable voices.

    Older audio APIs may not expose the capability endpoint; in that case the
    established process-default batch provider remains the compatibility path.
    """

    try:
        payload = _request_json(
            f"{base_url}/api/tts/stream/info",
            timeout_s=timeout_s,
        )
    except FixtureError:
        return ""
    providers = payload.get("providers")
    if type(providers) is not list:
        return ""
    default = payload.get("default")
    ordered = sorted(
        (item for item in providers if type(item) is dict),
        key=lambda item: item.get("id") != default,
    )
    for item in ordered:
        provider = item.get("id")
        if (
            item.get("available") is not True
            or not isinstance(provider, str)
            or not re.fullmatch(r"[a-z0-9_-]{1,32}", provider)
        ):
            continue
        try:
            _select_voices(_voice_catalog({"voices": item.get("voices")}))
        except FixtureError:
            continue
        return provider
    return ""


def _decode_audio(
    payload: dict[str, Any],
    *,
    expected_voice: str,
) -> tuple[bytes, str, str]:
    required = ("audio", "format", "provider", "model", "voice_id")
    if any(key not in payload for key in required):
        raise FixtureError("TTS response schema is invalid")
    if payload.get("format") != "wav":
        raise FixtureError("TTS response format must be wav")
    if payload.get("voice_id") != expected_voice:
        raise FixtureError("TTS response did not use the requested voice")
    provider = payload.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise FixtureError("TTS response provider is missing")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise FixtureError("TTS response model is missing")
    encoded = payload.get("audio")
    if not isinstance(encoded, str) or not encoded:
        raise FixtureError("TTS response audio is empty")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise FixtureError("TTS response audio is not valid base64") from exc
    if not raw:
        raise FixtureError("TTS response audio is empty")
    if len(raw) > _MAX_AUDIO_BYTES:
        raise FixtureError("TTS response audio is oversized")
    return raw, provider.strip(), model.strip()


def _wav_to_pcm16_mono(wav_bytes: bytes) -> bytes:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.getnframes()
            compression = source.getcomptype()
            if compression != "NONE":
                raise FixtureError(
                    "TTS response WAV must be uncompressed PCM",
                )
            if sample_width != 2:
                raise FixtureError(
                    "TTS response WAV must contain 16-bit samples",
                )
            if channels not in {1, 2}:
                raise FixtureError(
                    "TTS response WAV must be mono or stereo",
                )
            if not (
                MIN_WAV_SAMPLE_RATE_HZ
                <= sample_rate
                <= MAX_WAV_SAMPLE_RATE_HZ
            ):
                raise FixtureError(
                    "TTS response WAV sample rate is invalid",
                )
            if frames <= 0:
                raise FixtureError("TTS response audio is empty")
            if frames > sample_rate * MAX_WAV_DURATION_S:
                raise FixtureError(
                    "TTS response WAV duration is oversized",
                )
            frame_bytes = channels * sample_width
            expected_input_bytes = frames * frame_bytes
            if expected_input_bytes > MAX_PCM_INPUT_BYTES:
                raise FixtureError(
                    "TTS response WAV input is oversized",
                )
            output_frames = int(
                round(frames * SAMPLE_RATE_HZ / sample_rate),
            )
            if (
                output_frames <= 0
                or output_frames * (BIT_DEPTH // 8)
                > MAX_NORMALIZED_PCM_BYTES
            ):
                raise FixtureError(
                    "normalized PCM output is oversized",
                )
            payload = source.readframes(frames)
    except FixtureError:
        raise
    except (MemoryError, OverflowError) as exc:
        raise FixtureError(
            "TTS response WAV resource limit was exceeded",
        ) from exc
    except (wave.Error, EOFError, OSError) as exc:
        raise FixtureError("TTS response is not a valid PCM WAV") from exc
    if not payload:
        raise FixtureError("TTS response audio is empty")
    if (
        len(payload) != expected_input_bytes
        or len(payload) % frame_bytes
    ):
        raise FixtureError("TTS response WAV frame payload is truncated")
    try:
        samples = array.array("h")
        samples.frombytes(payload)
    except (MemoryError, OverflowError, ValueError) as exc:
        raise FixtureError(
            "TTS response normalization resource limit was exceeded",
        ) from exc
    if sys.byteorder != "little":
        samples.byteswap()
    if channels == 2:
        try:
            samples = array.array(
                "h",
                (
                    (int(samples[index]) + int(samples[index + 1])) // 2
                    for index in range(0, len(samples), 2)
                ),
            )
        except (MemoryError, OverflowError, ValueError) as exc:
            raise FixtureError(
                "TTS response normalization resource limit was exceeded",
            ) from exc
    if not samples:
        raise FixtureError("TTS response audio is empty")
    if sample_rate != SAMPLE_RATE_HZ:
        try:
            resampled = array.array("h")
            ratio = sample_rate / SAMPLE_RATE_HZ
            for output_index in range(output_frames):
                source_position = output_index * ratio
                left = int(source_position)
                fraction = source_position - left
                if left >= len(samples) - 1:
                    sample = int(samples[-1])
                else:
                    sample = round(
                        int(samples[left]) * (1.0 - fraction)
                        + int(samples[left + 1]) * fraction,
                    )
                resampled.append(max(-32768, min(32767, sample)))
        except (MemoryError, OverflowError, ValueError) as exc:
            raise FixtureError(
                "TTS response normalization resource limit was exceeded",
            ) from exc
        samples = resampled
    if sys.byteorder != "little":
        samples.byteswap()
    try:
        pcm = samples.tobytes()
    except (MemoryError, OverflowError) as exc:
        raise FixtureError(
            "TTS response normalization resource limit was exceeded",
        ) from exc
    if not pcm or len(pcm) % 2:
        raise FixtureError("normalized PCM output is invalid")
    return pcm


def _exclusive_artifact_dir(artifact_dir: Path) -> Path:
    if artifact_dir.is_symlink():
        raise FixtureError("artifact directory cannot be a symbolic link")
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FixtureError("artifact directory could not be created") from exc
    try:
        resolved = artifact_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FixtureError("artifact directory could not be resolved") from exc
    if not resolved.is_dir():
        raise FixtureError("artifact path must be a directory")
    try:
        if any(resolved.iterdir()):
            raise FixtureError("artifact directory must be empty and run-exclusive")
    except OSError as exc:
        raise FixtureError("artifact directory could not be inspected") from exc
    return resolved


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(payload)
    except OSError as exc:
        raise FixtureError("fixture file could not be written") from exc


def prepare_fixtures(
    artifact_dir: str | os.PathLike[str],
    *,
    audio_api_url: str = "http://localhost:50059",
    timeout_s: float = 90.0,
) -> Path:
    """Generate the frozen A/B fixture matrix in an exclusive directory."""

    root = _exclusive_artifact_dir(Path(artifact_dir))
    base_url = audio_api_url.rstrip("/")
    if not base_url:
        raise FixtureError("audio API URL is empty")
    provider_pin = _discover_stream_provider(
        base_url,
        timeout_s=timeout_s,
    )
    voice_url = f"{base_url}/api/voices"
    if provider_pin:
        voice_url += "?" + urllib.parse.urlencode(
            {"provider": provider_pin},
        )
    voices = _select_voices(
        _voice_catalog(
            _request_json(
                voice_url,
                timeout_s=timeout_s,
            ),
        ),
    )
    selected = tuple(
        {
            "slot": slot,
            "voice_id": voice["voice_id"],
            "language": voice["language"],
            "gender": voice["gender"],
        }
        for slot, voice in zip(("A", "B"), voices, strict=True)
    )
    text_matrix = (
        *((f"enroll_{index}", "enroll", text)
          for index, text in enumerate(ENROLL_TEXTS, start=1)),
        ("probe", "probe", PROBE_TEXT),
    )
    files: list[dict[str, Any]] = []
    providers: set[str] = set()
    models: set[str] = set()
    actual_voices: set[str] = set()
    normalized_by_text: dict[str, bytes] = {}
    for voice in selected:
        for text_key, purpose, text in text_matrix:
            request_body = {
                "text": text,
                "voice_id": voice["voice_id"],
                "format": "wav",
            }
            if provider_pin:
                request_body["provider"] = provider_pin
            response = _request_json(
                f"{base_url}/api/tts",
                body=request_body,
                timeout_s=timeout_s,
            )
            wav_bytes, provider, model = _decode_audio(
                response,
                expected_voice=voice["voice_id"],
            )
            pcm = _wav_to_pcm16_mono(wav_bytes)
            providers.add(provider)
            models.add(model)
            actual_voices.add(response["voice_id"])
            if voice["slot"] == "A":
                normalized_by_text[text_key] = pcm
            elif normalized_by_text.get(text_key) == pcm:
                raise FixtureError(
                    "TTS voices produced identical normalized audio",
                )
            filename = f"{voice['slot'].lower()}-{text_key.replace('_', '-')}.pcm"
            _write_bytes_exclusive(root / filename, pcm)
            files.append({
                "speaker": voice["slot"],
                "purpose": purpose,
                "text_key": text_key,
                "path": filename,
                "bytes": len(pcm),
                "sha256": hashlib.sha256(pcm).hexdigest(),
            })
    if len(providers) != 1:
        raise FixtureError("TTS provider changed during fixture generation")
    if len(models) != 1:
        raise FixtureError("TTS model changed during fixture generation")
    if len(actual_voices) != 2:
        raise FixtureError("TTS did not produce two distinct usable voices")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "provider": next(iter(providers)),
        "model": next(iter(models)),
        "voices": list(selected),
        "texts": {
            "enroll": list(ENROLL_TEXTS),
            "probe": PROBE_TEXT,
        },
        "audio": {
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "channels": CHANNELS,
            "bit_depth": BIT_DEPTH,
            "encoding": ENCODING,
        },
        "files": files,
        "generated_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        ),
        "synthetic_functional_only": True,
        "no_human_biometric": True,
    }
    manifest_path = root / MANIFEST_NAME
    try:
        with manifest_path.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(
                manifest,
                output,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output.write("\n")
    except OSError as exc:
        raise FixtureError("fixture manifest could not be written") from exc
    verify_fixtures(root)
    return manifest_path


def _safe_relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FixtureError("fixture file path is invalid")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in posix.parts)
        or len(posix.parts) != 1
    ):
        raise FixtureError("fixture file path must be a safe relative filename")
    return posix


def _read_manifest_bytes(manifest_path: Path) -> bytes:
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FixtureError("fixture manifest is missing or unsafe")
    try:
        before = manifest_path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > _MAX_JSON_BYTES
        ):
            raise FixtureError("fixture manifest JSON is empty or oversized")
        with manifest_path.open("rb") as source:
            raw = source.read(_MAX_JSON_BYTES + 1)
            if len(raw) > _MAX_JSON_BYTES or source.read(1):
                raise FixtureError(
                    "fixture manifest JSON is empty or oversized",
                )
    except OSError as exc:
        raise FixtureError("fixture manifest could not be read") from exc
    if len(raw) != before.st_size:
        raise FixtureError("fixture manifest changed during read")
    return raw


def fixture_manifest_sha256(manifest_path: Path) -> str:
    return hashlib.sha256(
        _read_manifest_bytes(Path(manifest_path)),
    ).hexdigest()


def _load_manifest(
    root: Path,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    raw = _read_manifest_bytes(root / MANIFEST_NAME)
    if (
        expected_sha256 is not None
        and (
            _SHA256_RE.fullmatch(expected_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_sha256
        )
    ):
        raise FixtureError("fixture manifest changed after owner attestation")
    return _load_json_bytes(raw, label="fixture manifest")


def read_verified_pcm(
    path: Path,
    *,
    declared_bytes: int,
    declared_sha256: str,
) -> bytes:
    """Bounded exact read of one manifest-declared verified PCM file."""

    candidate = Path(path)
    if (
        type(declared_bytes) is not int
        or declared_bytes <= 0
        or declared_bytes > MAX_NORMALIZED_PCM_BYTES
        or declared_bytes % 2
        or not isinstance(declared_sha256, str)
        or _SHA256_RE.fullmatch(declared_sha256) is None
        or candidate.is_symlink()
    ):
        raise FixtureError("fixture PCM declaration is invalid or unsafe")
    try:
        before = candidate.lstat()
    except OSError as exc:
        raise FixtureError("fixture PCM file could not be inspected") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size != declared_bytes
    ):
        raise FixtureError("fixture PCM file changed after verification")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as source:
            opened = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != declared_bytes
                or (
                    before.st_ino
                    and opened.st_ino
                    and before.st_ino != opened.st_ino
                )
            ):
                raise FixtureError(
                    "fixture PCM file changed after verification",
                )
            payload = source.read(declared_bytes + 1)
            if len(payload) != declared_bytes or source.read(1):
                raise FixtureError(
                    "fixture PCM file changed after verification",
                )
            finished = os.fstat(source.fileno())
    except FixtureError:
        raise
    except OSError as exc:
        raise FixtureError("fixture PCM file could not be read") from exc
    if (
        finished.st_size != opened.st_size
        or getattr(finished, "st_mtime_ns", None)
        != getattr(opened, "st_mtime_ns", None)
        or hashlib.sha256(payload).hexdigest() != declared_sha256
    ):
        raise FixtureError("fixture PCM file changed after verification")
    return payload


def _validate_manifest_schema(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    _require_exact_keys(manifest, _TOP_LEVEL_KEYS, label="fixture manifest")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise FixtureError("fixture manifest schema version is unsupported")
    if (
        not isinstance(manifest["provider"], str)
        or not manifest["provider"].strip()
    ):
        raise FixtureError("fixture manifest provider is invalid")
    if not isinstance(manifest["model"], str) or not manifest["model"].strip():
        raise FixtureError("fixture manifest model is invalid")
    voices = manifest["voices"]
    if type(voices) is not list or len(voices) != 2:
        raise FixtureError("fixture manifest voices schema is invalid")
    voice_ids: set[str] = set()
    for index, voice in enumerate(voices):
        if type(voice) is not dict:
            raise FixtureError("fixture manifest voice schema is invalid")
        _require_exact_keys(voice, _VOICE_KEYS, label="fixture manifest voice")
        if voice["slot"] != ("A", "B")[index]:
            raise FixtureError("fixture manifest voice slots are invalid")
        for key in ("voice_id", "language", "gender"):
            if not isinstance(voice[key], str):
                raise FixtureError("fixture manifest voice value is invalid")
        if not voice["voice_id"] or voice["voice_id"] in voice_ids:
            raise FixtureError("fixture manifest voice IDs must be distinct")
        voice_ids.add(voice["voice_id"])

    texts = manifest["texts"]
    if type(texts) is not dict:
        raise FixtureError("fixture manifest texts schema is invalid")
    _require_exact_keys(texts, _TEXT_KEYS, label="fixture manifest texts")
    if texts["enroll"] != list(ENROLL_TEXTS) or texts["probe"] != PROBE_TEXT:
        raise FixtureError("fixture manifest frozen texts do not match")

    audio = manifest["audio"]
    if type(audio) is not dict:
        raise FixtureError("fixture manifest audio schema is invalid")
    _require_exact_keys(audio, _AUDIO_KEYS, label="fixture manifest audio")
    if audio != {
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "channels": CHANNELS,
        "bit_depth": BIT_DEPTH,
        "encoding": ENCODING,
    }:
        raise FixtureError("fixture manifest PCM format is invalid")
    if manifest["synthetic_functional_only"] is not True:
        raise FixtureError("fixture manifest synthetic-only flag is invalid")
    if manifest["no_human_biometric"] is not True:
        raise FixtureError("fixture manifest biometric flag is invalid")
    generated_at = manifest["generated_at"]
    if not isinstance(generated_at, str) or not generated_at.endswith("Z"):
        raise FixtureError("fixture manifest generation timestamp is invalid")
    try:
        datetime.fromisoformat(generated_at[:-1] + "+00:00")
    except ValueError as exc:
        raise FixtureError("fixture manifest generation timestamp is invalid") from exc

    files = manifest["files"]
    if type(files) is not list or len(files) != 8:
        raise FixtureError("fixture manifest must list exactly eight files")
    expected_matrix = {
        (speaker, "enroll", f"enroll_{index}")
        for speaker in ("A", "B")
        for index in range(1, 4)
    } | {
        ("A", "probe", "probe"),
        ("B", "probe", "probe"),
    }
    seen_matrix: set[tuple[str, str, str]] = set()
    seen_paths: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in files:
        if type(item) is not dict:
            raise FixtureError("fixture manifest file schema is invalid")
        _require_exact_keys(item, _FILE_KEYS, label="fixture manifest file")
        matrix_key = (item["speaker"], item["purpose"], item["text_key"])
        if matrix_key not in expected_matrix or matrix_key in seen_matrix:
            raise FixtureError("fixture manifest file matrix is invalid")
        seen_matrix.add(matrix_key)
        relative = _safe_relative_path(item["path"])
        path_text = relative.as_posix()
        if path_text in seen_paths:
            raise FixtureError("fixture manifest contains a duplicate file path")
        seen_paths.add(path_text)
        if type(item["bytes"]) is not int or item["bytes"] <= 0:
            raise FixtureError("fixture manifest file byte count is invalid")
        if (
            not isinstance(item["sha256"], str)
            or _SHA256_RE.fullmatch(item["sha256"]) is None
        ):
            raise FixtureError("fixture manifest file hash is invalid")
        normalized.append({**item, "path": path_text})
    if seen_matrix != expected_matrix:
        raise FixtureError("fixture manifest file matrix is incomplete")
    return normalized


def verify_fixtures(
    artifact_dir: str | os.PathLike[str],
) -> Path:
    """Verify a fixture directory offline using only its strict manifest."""

    requested = Path(artifact_dir)
    if requested.is_symlink():
        raise FixtureError("artifact directory cannot be a symbolic link")
    try:
        root = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FixtureError("artifact directory is missing") from exc
    if not root.is_dir():
        raise FixtureError("artifact path must be a directory")
    manifest = _load_manifest(root)
    files = _validate_manifest_schema(manifest)
    expected_names = {MANIFEST_NAME, *(item["path"] for item in files)}
    try:
        actual_entries = list(root.iterdir())
    except OSError as exc:
        raise FixtureError("fixture directory could not be inspected") from exc
    actual_names = {entry.name for entry in actual_entries}
    missing = expected_names - actual_names
    extra = actual_names - expected_names
    if missing:
        raise FixtureError("fixture directory has missing files")
    if extra:
        raise FixtureError("fixture directory has extra files")
    for entry in actual_entries:
        if entry.is_symlink() or not entry.is_file():
            raise FixtureError("fixture directory contains an unsafe entry")
    for item in files:
        path = root / item["path"]
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise FixtureError("fixture file could not be inspected") from exc
        if (
            size <= 0
            or size % 2
            or size != item["bytes"]
        ):
            raise FixtureError("fixture file bytes do not match the manifest")
        if size > MAX_NORMALIZED_PCM_BYTES:
            raise FixtureError("fixture PCM file is oversized")
        digest = hashlib.sha256()
        total = 0
        try:
            with path.open("rb") as source:
                while True:
                    chunk = source.read(_HASH_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_NORMALIZED_PCM_BYTES:
                        raise FixtureError("fixture PCM file is oversized")
                    digest.update(chunk)
        except FixtureError:
            raise
        except OSError as exc:
            raise FixtureError("fixture file could not be read") from exc
        if total != size:
            raise FixtureError("fixture file changed during verification")
        if digest.hexdigest() != item["sha256"]:
            raise FixtureError("fixture file hash does not match the manifest")
    return root / MANIFEST_NAME


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare or offline-verify synthetic voiceprint fixtures",
    )
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument(
        "--audio-api-url",
        default=os.getenv("AUDIO_API_URL", "http://localhost:50059"),
    )
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument("--verify", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verify:
            manifest = verify_fixtures(args.artifact_dir)
        else:
            manifest = prepare_fixtures(
                args.artifact_dir,
                audio_api_url=args.audio_api_url,
                timeout_s=args.timeout_s,
            )
    except FixtureError as exc:
        print(f"voiceprint fixture preparation failed: {exc}", file=sys.stderr)
        return 1
    print(str(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
