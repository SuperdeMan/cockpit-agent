"""时刻与时间窗的**确定性**解析（G1 时间约束求解的共享实现）。

三件事，各有明确边界：
- `parse_clock_time`：中文/数字时刻 → epoch 秒（原 navigation `_parse_arrive_by`，
  行为逐字不变）。**唯一实现**——nearby 要用同一套消歧语义，判定抄两份正是 B1 那个
  bug 的成因（CLAUDE.md §6）。
- `parse_event_time`：**事件时刻**（「晚上7点的电影」「7点半那场话剧」）。与到达时限
  （「5点前到」）刻意互斥：后者归 navigation 的 `arrive_by`，两条链不许抢同一句。
- `dining_window`：由事件时刻**反推**用餐窗（入座/离席/事件）。路上时间是**明说的
  假设**不是伪造的数据——调用方必须把它念出来（本项目的诚实降级形态）。

纯函数、零 I/O，`now_ts` 可注入供测试。
"""
from __future__ import annotations

import re
import time

from runtime.clock import epoch_at, hhmm, local_struct, minutes_of

_CN_HOUR = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
            "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
# 时刻本体：HH:MM 或 N点[半|N分]，可带段位前缀（槽位值与原话片段共用）。
# 数字时刻用 \d{1,2}（「10点」「23点」是两位——单字符类会把「10点」错拆成「0点」）。
_CLOCK_RE = re.compile(
    r"(?:(上午|早上|凌晨|中午|下午|傍晚|晚上)\s*)?"
    r"(?:(\d{1,2})[:：](\d{2})|(十一|十二|\d{1,2}|[一两二三四五六七八九十])\s*点\s*(半|\d{1,2}分)?)")


def parse_clock_time(text: str, now_ts: int | None = None) -> int | None:
    """「时刻」→ epoch 秒；解析不出返回 None。纯确定性，now 可注入供测试。

    裸 1-11 点无段位按「未来最近一次」消歧：14:00 说「5点前到」= 今天 17:00；
    20:00 说「5点前到」= 次日 05:00。带段位/24h 时刻过点即滚到明天同刻。
    """
    m = _CLOCK_RE.search(text or "")
    if not m:
        return None
    seg, hh, mm, cn, cn_min = m.groups()
    if hh is not None:
        hour, minute = int(hh), int(mm)
    else:
        hour = int(cn) if cn.isdigit() else _CN_HOUR.get(cn, -1)
        minute = 30 if cn_min == "半" else (int(cn_min[:-1]) if cn_min else 0)
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    now = int(now_ts if now_ts is not None else time.time())
    # ⚠ 墙钟必须按**业务时区**取（容器 TZ=UTC）：裸 localtime 会让「晚上7点」
    # 变成 19:00 UTC=次日 03:00 北京，而宿主 UTC+8 跑单测永远不红。
    lt = local_struct(now)

    def _at(day_off: int, h: int) -> int:
        return epoch_at(lt.tm_year, lt.tm_mon, lt.tm_mday + day_off, h % 24, minute)

    if seg in ("下午", "傍晚", "晚上") and hour < 12:
        hour += 12
    elif seg == "中午" and hour < 6:
        hour += 12
    if seg or hour >= 12 or hour == 0:
        ts = _at(0, hour)
        return ts if ts > now else _at(1, hour)
    cands = [t for t in (_at(0, hour), _at(0, hour + 12)) if t > now]
    return min(cands) if cands else _at(1, hour)


# ── 事件时刻（G1 余项：反推窗的输入）──────────────────────────────
# 只认「有场次/班次概念」的事件词——它们才隐含「必须在那之前到」。
# 刻意不含「饭/午饭/晚饭」：那是被反推的对象本身，含进来会自我循环。
_EVENT_WORDS = ("电影", "影片", "场次", "演出", "话剧", "音乐会", "演唱会", "音乐节",
                "球赛", "比赛", "决赛", "讲座", "发布会", "展览", "秀",
                "航班", "飞机", "火车", "高铁", "动车", "列车",
                "会议", "开会", "面试", "婚礼", "party", "聚会", "约会")
_EVENT_RE = re.compile("|".join(_EVENT_WORDS))
# 时刻与事件词的最大间隔（字符）：「晚上7点的电影」间隔 1、「7点半有场话剧」间隔 3、
# 「电影是晚上7点」事件在前。放宽到 12 足够覆盖口语插入语，又不至于把整句里
# 八竿子打不着的两个词凑成一对。
_EVENT_GAP = 12


def parse_event_time(text: str, now_ts: int | None = None) -> tuple[int, str] | None:
    """「时刻 + 事件词」→ (事件 epoch 秒, 事件词)；不成对返回 None。

    与到达时限互斥：「5点前到」「五点我要到」不含事件词 → 本函数不认（归
    navigation 的 `arrive_by`）。一句里两者都有时各走各的链，不互相覆盖。
    """
    t = text or ""
    clock = _CLOCK_RE.search(t)
    if not clock:
        return None
    ev = None
    for m in _EVENT_RE.finditer(t):
        gap = (m.start() - clock.end()) if m.start() >= clock.end() else (clock.start() - m.end())
        if 0 <= gap <= _EVENT_GAP:
            ev = m
            break
    if ev is None:
        return None
    ts = parse_clock_time(clock.group(0), now_ts=now_ts)
    return (ts, ev.group(0)) if ts else None


# 默认用餐时长与路上预留：**是假设不是数据**，调用方必须把它念给用户听。
DINING_DWELL_MIN = 60
DINING_BUFFER_MIN = 30


def dining_window(event_ts: int, *, dwell_min: int = DINING_DWELL_MIN,
                  buffer_min: int = DINING_BUFFER_MIN,
                  now_ts: int | None = None) -> dict:
    """由事件时刻反推用餐窗 → {seat_ts, leave_ts, event_ts, dwell_min, buffer_min, tight}。

    `leave = event - buffer`（路上预留）、`seat = leave - dwell`（用餐时长）。
    `tight=True` 表示按这个窗口已经来不及（入座时刻不在未来）——调用方必须**如实说
    来不及**，不许把窗口压缩到编出一个能凑上的数。
    """
    now = int(now_ts if now_ts is not None else time.time())
    leave = int(event_ts) - buffer_min * 60
    seat = leave - dwell_min * 60
    return {"seat_ts": seat, "leave_ts": leave, "event_ts": int(event_ts),
            "dwell_min": dwell_min, "buffer_min": buffer_min,
            "tight": seat <= now}


def fmt_clock(ts) -> str:
    """epoch → 业务时区「HH:MM」。播给用户的时刻一律走这里。"""
    return hhmm(int(ts))


def clock_minutes(ts) -> int:
    """epoch → 当天的「时:分」折算分钟（供营业时段判定注入 now_min）。

    ⚠ 必须与 `providers/base.is_open_now` 同一时区——它按 UTC+8 判营业时段，
    这里若按容器本地时（UTC）算，筛出来的「营业中」会整体错 8 小时。"""
    return minutes_of(int(ts))
