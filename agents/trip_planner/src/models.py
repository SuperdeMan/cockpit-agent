"""结构化行程数据模型（P0）。

把行程从「LLM 自由文本」升级为结构化可执行对象：Trip → Day → Stop/Leg。
所有卡片、导航、修改、在途操作都作用在这个对象上。

序列化要点：
- `to_dict()` 用 `dataclasses.asdict` 递归转纯 dict——同一份序列化同时供 memory 持久化与
  `trip_itinerary` 卡（`card_dict()` = to_dict + type）。
- `from_dict()` 容错重建（memory 里可能是旧形状/部分字段），全部 `.get` 带默认。
- `Stop.poi=None` 表示**未接地**（搜不到真实 POI），绝不臆造坐标——诚实降级。
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict


@dataclass
class Stop:
    """一个停靠点（景点/餐/住/充电）。poi=None 表示未接地。"""
    stop_id: str = ""
    type: str = "attraction"        # attraction|meal|hotel|charging|custom
    name: str = ""                  # 地图官方名（接地后）；未接地时=骨架原始名
    poi: dict | None = None         # {id,name,address,lat,lng,rating}；None=未接地
    dwell_min: int = 90             # 预计停留时长（分钟）
    time_window: dict | None = None  # 可选 {start,end}
    grounded: bool = False
    source: str = "llm"             # llm|user|charging_solver
    note: str = ""

    @property
    def lat(self):
        return (self.poi or {}).get("lat")

    @property
    def lng(self):
        return (self.poi or {}).get("lng")


@dataclass
class Leg:
    """相邻 stop 间的驾驶段（含按 SoC 编织的充电点）。"""
    from_stop_id: str = ""
    to_stop_id: str = ""
    distance_km: float = 0.0
    drive_min: int = 0
    charging_stops: list = field(default_factory=list)  # [{name,address,lat,lng,at_km}]
    soc_before: int = 0
    soc_after: int = 0


@dataclass
class Day:
    """一天的行程：停靠点序列 + 段间驾驶。"""
    day_index: int = 1
    theme: str = ""
    date: str = ""
    stops: list = field(default_factory=list)   # list[Stop]
    legs: list = field(default_factory=list)    # list[Leg]
    weather: dict | None = None

    def grounded_stops(self) -> list:
        return [s for s in self.stops if isinstance(s, Stop) and s.grounded]


@dataclass
class Trip:
    """一次行程。P0 用 draft/confirmed；cursor/active 留 P2 在途。"""
    trip_id: str = ""
    session_id: str = ""
    user_id: str = ""
    destination: str = ""
    days: int = 0
    preferences: list = field(default_factory=list)
    status: str = "draft"           # draft|confirmed|active|completed
    cursor: dict = field(default_factory=lambda: {"day_index": 0, "stop_index": 0})
    ev: dict = field(default_factory=dict)      # {full_range_km, start_soc}
    itinerary: list = field(default_factory=list)  # list[Day]
    raw_text: str = ""

    # ── 序列化（memory 持久化 + 卡片共用同一份）──────────────────
    def to_dict(self) -> dict:
        return asdict(self)

    def card_dict(self) -> dict:
        """`trip_itinerary` 卡 = to_dict + type（ui_card 是自由 Struct，免改 proto）。"""
        return {"type": "trip_itinerary", "display_priority": 0, **self.to_dict()}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Trip":
        d = d or {}
        days_raw = d.get("itinerary") or []
        itinerary = [_day_from_dict(x) for x in days_raw if isinstance(x, dict)]
        return cls(
            trip_id=d.get("trip_id", "") or "",
            session_id=d.get("session_id", "") or "",
            user_id=d.get("user_id", "") or "",
            destination=d.get("destination", "") or "",
            days=int(d.get("days", 0) or 0),
            preferences=list(d.get("preferences") or []),
            status=d.get("status", "draft") or "draft",
            cursor=dict(d.get("cursor") or {"day_index": 0, "stop_index": 0}),
            ev=dict(d.get("ev") or {}),
            itinerary=itinerary,
            raw_text=d.get("raw_text", "") or "",
        )

    # ── 便捷读取 ──────────────────────────────────────────────
    def day(self, day_index: int) -> "Day | None":
        for dy in self.itinerary:
            if isinstance(dy, Day) and dy.day_index == day_index:
                return dy
        return None

    def first_stop(self) -> "Stop | None":
        """行程第一个已接地的停靠点（确认收尾时作导航第一站）。"""
        for dy in self.itinerary:
            for s in dy.grounded_stops():
                return s
        return None


def _stop_from_dict(d: dict) -> Stop:
    return Stop(
        stop_id=d.get("stop_id", "") or "",
        type=d.get("type", "attraction") or "attraction",
        name=d.get("name", "") or "",
        poi=d.get("poi") if isinstance(d.get("poi"), dict) else None,
        dwell_min=int(d.get("dwell_min", 90) or 0),
        time_window=d.get("time_window") if isinstance(d.get("time_window"), dict) else None,
        grounded=bool(d.get("grounded", False)),
        source=d.get("source", "llm") or "llm",
        note=d.get("note", "") or "",
    )


def _leg_from_dict(d: dict) -> Leg:
    return Leg(
        from_stop_id=d.get("from_stop_id", "") or "",
        to_stop_id=d.get("to_stop_id", "") or "",
        distance_km=float(d.get("distance_km", 0) or 0),
        drive_min=int(d.get("drive_min", 0) or 0),
        charging_stops=list(d.get("charging_stops") or []),
        soc_before=int(d.get("soc_before", 0) or 0),
        soc_after=int(d.get("soc_after", 0) or 0),
    )


def _day_from_dict(d: dict) -> Day:
    return Day(
        day_index=int(d.get("day_index", 1) or 1),
        theme=d.get("theme", "") or "",
        date=d.get("date", "") or "",
        stops=[_stop_from_dict(x) for x in (d.get("stops") or []) if isinstance(x, dict)],
        legs=[_leg_from_dict(x) for x in (d.get("legs") or []) if isinstance(x, dict)],
        weather=d.get("weather") if isinstance(d.get("weather"), dict) else None,
    )
