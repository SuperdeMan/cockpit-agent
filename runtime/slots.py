"""Shared normalization for untrusted planner slot shapes."""
from __future__ import annotations

import json

# 规划模型偶发把「用当前位置」写成 city 槽的**占位值**（prompt 明令「绝不编造占位值（如『当前位置』
# 『未知』）」，MiniMax-M3 真栈 2026-09-16 两轮连着写了 `city: "当前位置"`）。消费方 `_resolve_city`
# 一律优先信 city 槽 ⇒ 占位值被当城市名拿去和风 GeoAPI 查 ⇒ 400 No Such Location ⇒ 用户听到
# 「没查到「当前位置」的天气」，而这一轮 meta 里明明带着坐标。判据放在唯一的归一入口：占位值
# 就是「没给城市」，让坐标 / 追问那条既有链路接手；`focus.last_city` 也因此不会被它污染。
# 只收**指代当前位置 / 明示未知**的词，不收任何真实地名。
_CITY_PLACEHOLDERS = frozenset({
    "当前位置", "当前地点", "当前城市", "当前所在地", "现在的位置", "现在位置", "所在位置", "所在地", "所在城市",
    "我的位置", "我的城市", "我所在的城市", "我所在的位置", "我这里", "我这边",
    "这里", "这儿", "这边", "此地", "此处", "本地", "本市", "当地", "附近", "周边",
    "未知", "不详", "不确定", "无", "空",
    "here", "current", "current location", "current city", "my location", "my city", "nearby",
    "unknown", "none", "null", "n/a", "na",
})


def _reject_placeholder(city: str) -> str:
    return "" if city.strip().lower() in _CITY_PLACEHOLDERS else city


def normalize_city_slot(value) -> str:
    """Return a city scalar; reject malformed/object values and placeholders fail-closed."""
    if isinstance(value, dict):
        value = value.get("city", "")
        return _reject_placeholder(value.strip()) if isinstance(value, str) else ""
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw.startswith("{"):
        return _reject_placeholder(raw)
    if not raw.endswith("}"):
        return ""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(decoded, dict):
        return ""
    city = decoded.get("city", "")
    return _reject_placeholder(city.strip()) if isinstance(city, str) else ""
