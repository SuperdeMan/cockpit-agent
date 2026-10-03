"""车况读数连同时效与来源（CA2-19 S1）。

车况信封（`runtime/vehicle_state.py`）给每个信号带了来源类型、是否验签、观测时间与到期时间；权限视图
（`context_access.vehicle_projection`）只放行「good + 有界时效 + 未过期」的信号。下游此前只拿裸数值，
于是读不到时各处自己补缺省（电量按 50%、端侧按 0），说出口的读数也不带「多旧、从哪来」。

一份读法，云端 Agent、低电量提醒与端侧共用：
- `Reading.value is None` ⇔ 没读到（没权限、没信号、已过期）——不是 0，也不是任何缺省值；0 是合法读数。
- `note()`：只在来自非实车（模拟车 / 沙箱）或读数较旧（超过 `STALE_AFTER_S`）时给出，否则空串；话术用 `spoken_note()`。
- `card_fields()`：卡片与数据一律带来源类型、观测时间（业务时区）、读数年龄和同一句说明。
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from runtime.clock import local_dt

#: 读数多旧算「较旧」，话术要点明。电量信号的有效期是 180 秒，过期的根本进不来。
STALE_AFTER_S = 60
_KIND_LABEL = {"simulated": "模拟车", "sandbox": "沙箱"}


def _now_ms(now_ms=None) -> float:
    return time.time() * 1000 if now_ms is None else float(now_ms)


@dataclass(frozen=True)
class Reading:
    key: str
    value: object = None
    observed_at_ms: float | None = None
    source_kind: str = ""
    authenticated: bool = False

    @property
    def known(self) -> bool:
        return self.value is not None

    def percent(self) -> int | None:
        """百分比类读数（电量）：去掉百分号后取整、夹在 0–100；读不到或不是数就是 None。"""
        if self.value is None or isinstance(self.value, bool):
            return None
        try:
            number = float(str(self.value).replace("%", "").strip())
        except ValueError:
            return None
        if number != number:          # NaN
            return None
        return int(round(min(100.0, max(0.0, number))))

    def age_s(self, now_ms=None) -> int | None:
        if self.observed_at_ms is None:
            return None
        return max(0, int((_now_ms(now_ms) - float(self.observed_at_ms)) / 1000))

    def note(self, now_ms=None) -> str:
        return source_note(self.source_kind, self.age_s(now_ms))

    def spoken_note(self, now_ms=None) -> str:
        note = self.note(now_ms)
        return f"（{note}）" if note else ""

    def card_fields(self, prefix: str, now_ms=None) -> dict:
        """`{prefix}_source` 与 `{prefix}_note`；读不到时只给 `{prefix}_note="没读到"`。"""
        if not self.known:
            return {f"{prefix}_note": "没读到"}
        source = {"kind": self.source_kind or "unknown", "authenticated": self.authenticated}
        if self.observed_at_ms is not None:
            source["observed_at"] = local_dt(float(self.observed_at_ms) / 1000).isoformat(timespec="seconds")
            source["age_s"] = self.age_s(now_ms)
        fields = {f"{prefix}_source": source}
        note = self.note(now_ms)
        if note:
            fields[f"{prefix}_note"] = note
        return fields


def source_note(source_kind: str, age_s: int | None = None) -> str:
    """「模拟车读数」「约2分钟前的读数」「模拟车约2分钟前的读数」或空串（实车且新鲜）。"""
    label = _KIND_LABEL.get(str(source_kind or ""), "")
    stale = age_s is not None and age_s > STALE_AFTER_S
    if not label and not stale:
        return ""
    when = ""
    if stale:
        minutes = max(1, round(age_s / 60))
        when = f"约{minutes}分钟前的"
    return f"{label}{when}读数"


def from_signals(key: str, values: dict, signals: dict) -> Reading:
    """由（值、信号元数据）构造读数；值不在就是没读到。"""
    if not isinstance(values, dict) or key not in values or values[key] is None:
        return Reading(key)
    signal = (signals or {}).get(key) or {}
    observed = signal.get("observed_at_ms")
    return Reading(key, values[key],
                   float(observed) if type(observed) in (int, float) else None,
                   str(signal.get("source_kind") or ""), bool(signal.get("authenticated")))


def from_projection(meta: dict, vehicle_id: str, key: str, *, now_ms=None) -> Reading:
    """从编排下发的车况视图读（只认验签后、未过期的信号）。"""
    from runtime.context_access import vehicle_projection
    values, signals = vehicle_projection(meta or {}, vehicle_id, now_ms=now_ms)
    return from_signals(key, values, signals)


def from_view(view: dict, key: str) -> Reading:
    """从车况仓库的 `view()`（state + signals）读；质量不是 good 的值本来就不在 state 里。"""
    return from_signals(key, (view or {}).get("state") or {}, (view or {}).get("signals") or {})
