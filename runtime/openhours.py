"""营业时间窗口解析——**全仓唯一实现**（QA Q2 残余，2026-08-19）。

## 为什么住在 runtime/

落点判据是 CLAUDE.md §3 的**镜像依赖闭包**：这份解析有两个消费方，分别在两个
不同的镜像里——

- `agents/nearby`（`is_open_now`：按今日营业时间筛「此刻还开着的」）；
- `orchestrator/cloud`（`closing_minute`：算「这几家里哪家最晚关门」，
  Q2 残余的确定性消费方）。

两个镜像都 `COPY runtime`，都不 COPY 对方。所以要么住这里，要么变成两份
——而 `runtime/clock.py` 开篇那条判据已经把代价写清楚了：
**同一件事有三份各自正确的实现，就迟早会有第四份是错的**（cntime 那族已经应验过
一次：timeparse / timewindow / weather 三份对同一句话给出三个答案）。

## 数据长什么样

高德 `business.opentime_today` / `opentime_week` 的真实取值形态：
`"09:00-22:00"` / `"10:00-14:00 17:00-22:00"`（多段）/ `"24小时"` /
`"周一至周日 10:00-22:00"` / `"17:00-02:00"`（**跨零点**）。
判不出来一律返回 None——**未知不是「不营业」，也不是「营业到 0 点」**。
"""
from __future__ import annotations

import re
from datetime import datetime

from runtime.clock import BUSINESS_TZ

#: 一天的分钟数。跨零点的收盘时刻表达成 `end + DAY_MINUTES`，
#: 于是「营业到凌晨 2 点」= 1560 比「营业到 23 点」= 1380 更晚，**数值可直接比大小**。
DAY_MINUTES = 24 * 60

#: 「几点到几点」。分隔符四种都见过（`-` / `~` / `到` / `至`）。
_RANGE_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-~到至]\s*(\d{1,2}):(\d{2})")
#: 「全天营业」的三种写法。它们不带可解析的范围，所以要单独认。
_ALL_DAY_RE = re.compile(r"24\s*小时|全天|00:00\s*[-~到至]\s*24:00")


def parse_ranges(text: str | None) -> list[tuple[int, int]]:
    """→ 归一后的营业区间 `[(起, 止)]`，单位分钟，**止可能 > 1440**（跨零点）。

    `is_open_now` 与 `closing_minute` 共用它——两个问题、一份解析。
    没有可解析区间时返回 `[]`（含「24小时」这种不带数字范围的写法）。
    """
    out: list[tuple[int, int]] = []
    for h1, m1, h2, m2 in _RANGE_RE.findall(str(text or "")):
        start = int(h1) * 60 + int(m1)
        end = int(h2) * 60 + int(m2)
        if end <= start:
            # 跨零点（`17:00-02:00`）。`10:00-10:00` 这种退化写法也走这里，
            # 与收敛前的 `if end <= start` 分支逐字同判——**这是等价迁移，不是行为变更**。
            end += DAY_MINUTES
        out.append((start, end))
    return out


def is_all_day(text: str | None) -> bool:
    return bool(_ALL_DAY_RE.search(str(text or "")))


def is_open_now(open_today: str, now_min: int | None = None) -> bool | None:
    """按今日营业时间判断此刻是否营业。返回 True/False；无法解析→None（未知）。

    `now_min`: 当前「时:分」折算分钟（测试注入）；缺省取业务时区墙钟。

    ⚠ 2026-08-19 从 `agents/nearby/src/providers/base.py` 收敛来，**行为逐字等价**
    （见 `parse_ranges` 里那条跨零点注释）。nearby 侧改为转口引用。
    """
    s = (open_today or "").strip()
    if not s:
        return None
    if is_all_day(s):
        return True
    ranges = parse_ranges(s)
    if not ranges:
        return None
    if now_min is None:
        n = datetime.now(BUSINESS_TZ)
        now_min = n.hour * 60 + n.minute
    for start, end in ranges:
        if start <= now_min <= end:
            return True
        # 跨零点区间的**凌晨那一侧**：现在是 01:00 而营业到 02:00（=1560）。
        if end > DAY_MINUTES and now_min + DAY_MINUTES <= end:
            return True
    return False


def closing_minute(*candidates: str | None) -> int | None:
    """这一天**最晚几点关门**（分钟，跨零点 > 1440）。全判不出 → None。

    按参数顺序取**第一个能判出来的**（调用方按权威性排：`open_today` 优于
    `open_week`——今日实况比一周概述准）。多段营业取最晚那一段的收盘。
    全天营业 = `DAY_MINUTES`。

    **未知返回 None 而不是 0**：0 会被 `max()` 当成「凌晨 0 点关门」参与排序，
    于是一家「营业时间未知」的店会赢下「哪家最早关门」。同 `last_places_ts` 那条
    ——缺失值不许伪装成一个合法的极端值。
    """
    for raw in candidates:
        s = str(raw or "").strip()
        if not s:
            continue
        if is_all_day(s):
            return DAY_MINUTES
        ranges = parse_ranges(s)
        if ranges:
            return max(end for _, end in ranges)
    return None


def format_minute(minute: int) -> str:
    """把 `closing_minute` 的结果说成人话。跨零点说「次日 02:00」。"""
    day, rest = divmod(int(minute), DAY_MINUTES)
    if rest == 0 and day:
        return "24:00" if day == 1 else f"次日 {rest // 60:02d}:{rest % 60:02d}"
    stamp = f"{rest // 60:02d}:{rest % 60:02d}"
    return f"次日 {stamp}" if day else stamp
