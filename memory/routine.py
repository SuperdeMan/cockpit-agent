"""程序记忆雏形（P3）：从情景记忆聚合 routine（时间+地点+动作频次）→ 主动建议。

最小实现：情景事件的 value_json 携带 {action, place, hour}；按 (action,place,hour 桶)
聚合，频次达阈值即产出一条 procedural 记忆 + 一句主动建议（BMW"周一星巴克"那类）。

实际投递经项目已有 `agent.proactive` 通道（road-safety 样板）——本模块只产出建议，
不直接发 NATS（与现状"HMI 投递一跳待接"对齐）。
"""
from __future__ import annotations
import json
import time


def _hour_bucket(hour: int) -> str:
    if 5 <= hour < 11:
        return "早上"
    if 11 <= hour < 14:
        return "中午"
    if 14 <= hour < 18:
        return "下午"
    if 18 <= hour < 23:
        return "晚上"
    return "深夜"


def _parse_event(ep: dict) -> dict | None:
    """从情景记忆取结构化 {action, place, hour}。value_json 优先，缺失返回 None。"""
    raw = ep.get("value_json") or ""
    if not raw:
        return None
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    action = (v.get("action") or "").strip()
    place = (v.get("place") or "").strip()
    hour = v.get("hour")
    if not action or hour is None:
        return None
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        return None
    return {"action": action, "place": place, "hour": hour}


def detect_routines(episodes: list[dict], *, min_count: int = 3) -> list[dict]:
    """聚合情景事件为 routine 候选。返回 procedural 候选 dict 列表（含建议）。"""
    buckets: dict[tuple, list[dict]] = {}
    for ep in episodes or []:
        e = _parse_event(ep)
        if not e:
            continue
        key = (e["action"], e["place"], _hour_bucket(e["hour"]))
        buckets.setdefault(key, []).append(e)
    out = []
    for (action, place, tod), evs in buckets.items():
        if len(evs) < min_count:
            continue
        where = f"在{place}" if place else ""
        text = f"用户常在{tod}{where}{action}"
        suggestion = (f"您{tod}经常{where}{action}，需要现在为您{action}吗？"
                      if place else f"您{tod}经常{action}，需要现在安排吗？")
        out.append({
            "kind": "procedural",
            "predicate": f"routine.{action}.{place}.{tod}",
            "text": text,
            "scope": "procedural.routine",
            "provenance": "agent_inferred",
            "confidence": min(0.5 + 0.1 * (len(evs) - min_count), 0.9),
            "value_json": json.dumps({"action": action, "place": place, "tod": tod,
                                      "count": len(evs)}, ensure_ascii=False),
            "suggestion": suggestion,
        })
    return out


def now_hour() -> int:
    return time.localtime().tm_hour
