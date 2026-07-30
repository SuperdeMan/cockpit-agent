"""Shared TTS capability selection for live E2E probes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def select_tts_capability(
    info: Mapping[str, Any],
    *,
    preferred: Iterable[str] = ("minimax", "cosyvoice", "qwen", "mimo"),
    require_streaming: bool = False,
) -> tuple[str, str]:
    """Return one advertised, available provider and one of its voices."""

    providers = {
        item.get("id"): item
        for item in info.get("providers", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    order = tuple(dict.fromkeys((*preferred, info.get("default", ""))))
    for provider_id in order:
        item = providers.get(provider_id)
        if (
            not item
            or item.get("available") is False
            or (require_streaming and not item.get("streaming"))
        ):
            continue
        voices = item.get("voices")
        if not isinstance(voices, list) or not voices:
            continue
        voice = voices[0]
        if not isinstance(voice, Mapping):
            continue
        voice_id = voice.get("voice_id")
        if isinstance(voice_id, str) and voice_id:
            return provider_id, voice_id
    raise RuntimeError("no advertised TTS provider has an available voice")
