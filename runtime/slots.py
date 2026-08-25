"""Shared normalization for untrusted planner slot shapes."""
from __future__ import annotations

import json


def normalize_city_slot(value) -> str:
    """Return a city scalar; reject malformed/object values fail-closed."""
    if isinstance(value, dict):
        value = value.get("city", "")
        return value.strip() if isinstance(value, str) else ""
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw.startswith("{"):
        return raw
    if not raw.endswith("}"):
        return ""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(decoded, dict):
        return ""
    city = decoded.get("city", "")
    return city.strip() if isinstance(city, str) else ""
