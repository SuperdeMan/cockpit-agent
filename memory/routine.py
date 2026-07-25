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
    """从情景记忆取结构化 {action, place, hour, ts}。value_json 优先，缺失返回 None。"""
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
    # 发生时刻：优先 source_ts，退 valid_from（M2 P2 时间加权用；缺失=0 视作"没有时间信息"）
    try:
        ts = int(ep.get("source_ts") or ep.get("valid_from") or 0)
    except (TypeError, ValueError):
        ts = 0
    return {"action": action, "place": place, "hour": hour, "ts": ts}


# M2 P2：routine 的时间加权半衰期。比偏好的 90 天短得多——「习惯」本就该比「偏好」
# 更快过期：上个月天天去的健身房这个月一次没去，就不该再天天建议了。
ROUTINE_HALF_LIFE_DAYS = 30.0
# 「还没凉透」的门槛：有效计数至少要到 min_count 的这个比例。
# **刻意与 min_count 分成两段判据**——直接拿衰减后的有效计数比 min_count 会有边界坑：
# 连续三天做同一件事，衰减后是 2.93 < 3，用户感知就是「我明明连着三天都做了，它没认出来」。
# 正确语义是两件事：①真的发生够多次（裸频次 ≥ min_count）②这个习惯还活着（有效计数没凉透）。
RECENCY_FLOOR = 0.5


def detect_routines(episodes: list[dict], *, min_count: int = 3,
                    now: float | None = None) -> list[dict]:
    """聚合情景事件为 routine 候选。返回 procedural 候选 dict 列表（含建议）。

    **M2 P2 时间加权**：不再用裸频次比阈值，改用**按发生时刻衰减后的有效计数**
    （复用 weighting.decay，与偏好加权同一套语义）——「上个月密集出现但最近没有」的
    routine 自然沉底，不再拿陈年频次骚扰用户。

    无时间信息的事件（ts=0，存量数据）按 1.0 全额计入 → 行为与加权前一致（存量兼容）。
    """
    import time as _time
    import weighting
    now = _time.time() if now is None else now
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
            continue                        # ①真的发生够多次（裸频次，语义与加权前一致）
        effective = 0.0
        for e in evs:
            if not e["ts"]:
                effective += 1.0            # 无时间信息 → 全额（存量兼容）
            else:
                effective += weighting.decay(max(0.0, now - e["ts"]),
                                             ROUTINE_HALF_LIFE_DAYS)
        if effective < min_count * RECENCY_FLOOR:
            continue                        # ②习惯已经凉透 → 不再骚扰
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
            "confidence": min(0.5 + 0.1 * (effective - min_count), 0.9),
            "value_json": json.dumps({"action": action, "place": place, "tod": tod,
                                      "count": len(evs),
                                      "effective_count": round(effective, 2)},
                                     ensure_ascii=False),
            "suggestion": suggestion,
        })
    return out


def now_hour() -> int:
    return time.localtime().tm_hour
