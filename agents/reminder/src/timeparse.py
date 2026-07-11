"""中文时间表达 → epoch 的确定性解析（纯函数，注入 now/tz 可测）。

设计 §7 第 1 层：规则未命中返回 FAIL（agent 走 LLM 兜底）；只有日/段位没时刻返回
NEED_TIME（D5 追问）。时区默认 Asia/Shanghai，zoneinfo 不可用回退固定 UTC+8
（中国无夏令时，固定偏移恒正确；Windows 宿主无 tzdata 也能跑测试）。
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

OK = "ok"
NEED_TIME = "need_time"
FAIL = "fail"

_FIXED_CST = timezone(timedelta(hours=8), name="UTC+8")


def business_tz() -> tzinfo:
    name = os.getenv("REMINDER_TZ", "Asia/Shanghai")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return _FIXED_CST


@dataclass
class ParsedTime:
    status: str          # OK | NEED_TIME | FAIL
    fire_at: int = 0     # epoch 秒（UTC）
    display: str = ""    # 本地化回读："明天 08:00"


_CN_DIGIT = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2int(s: str | None) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        tens, _, ones = s.partition("十")
        t = 1 if tens == "" else _CN_DIGIT.get(tens)
        o = 0 if ones == "" else _CN_DIGIT.get(ones)
        return None if t is None or o is None else t * 10 + o
    return _CN_DIGIT.get(s) if len(s) == 1 else None


_NUM = r"(\d+|[一两二三四五六七八九十]+)"
_REL_RE = re.compile(_NUM + r"\s*个?\s*(半)?\s*(秒钟|秒|分钟|小时|钟头)\s*(?:以后|之后|后)")
_REL_HALF_RE = re.compile(r"半\s*个?\s*(小时|钟头)\s*(?:以后|之后|后)")
# 「过N分钟（再叫我）」：前缀"过"表相对，无"后"缀（P1a snooze 自然说法）
_REL_GUO_RE = re.compile(r"过\s*" + _NUM + r"\s*个?\s*(半)?\s*(秒钟|秒|分钟|小时|钟头)")
_REL_GUO_HALF_RE = re.compile(r"过\s*半\s*个?\s*(小时|钟头)")
# 长词在前（"大后天"含"后天"）；今晚/明早/明晚 自带段位
_DAY_WORDS = [("大后天", 3, ""), ("后天", 2, ""), ("明早", 1, "am"), ("明晚", 1, "eve"),
              ("明天", 1, ""), ("今晚", 0, "eve"), ("今天", 0, "")]
_WEEK_RE = re.compile(r"(下*)(?:个)?(?:周|星期|礼拜)([一二三四五六日天])")
_MD_RE = re.compile(_NUM + r"\s*月\s*" + _NUM + r"\s*[日号]")
_DOM_RE = re.compile(_NUM + r"\s*号")
_SEGS = [("凌晨", "dawn"), ("早上", "am"), ("早晨", "am"), ("上午", "am"), ("中午", "noon"),
         ("下午", "pm"), ("傍晚", "pm"), ("晚上", "eve"), ("夜里", "eve")]
_HHMM_RE = re.compile(r"([01]?\d|2[0-3])[:：]([0-5]\d)")
_CLOCK_RE = re.compile(_NUM + r"\s*点\s*(半|一刻|三刻|" + _NUM + r"\s*分?)?")


def _display(lt: datetime, ln: datetime) -> str:
    d = (lt.date() - ln.date()).days
    if d == 0:
        day = "今天"
    elif d == 1:
        day = "明天"
    elif d == 2:
        day = "后天"
    elif lt.year == ln.year:
        day = f"{lt.month}月{lt.day}日(周{'一二三四五六日'[lt.weekday()]})"
    else:
        day = f"{lt.year}年{lt.month}月{lt.day}日"
    return f"{day} {lt.hour:02d}:{lt.minute:02d}"


def format_display(fire_at: int, *, now: datetime | None = None,
                   tz: tzinfo | None = None) -> str:
    tz = tz or business_tz()
    now_utc = now or datetime.now(timezone.utc)
    return _display(datetime.fromtimestamp(fire_at, tz), now_utc.astimezone(tz))


def _ok(target_utc: datetime, ln: datetime, tz: tzinfo) -> ParsedTime:
    return ParsedTime(OK, int(target_utc.timestamp()),
                      _display(target_utc.astimezone(tz), ln))


def parse_time_text(text: str, *, now: datetime | None = None,
                    tz: tzinfo | None = None) -> ParsedTime:
    tz = tz or business_tz()
    now_utc = now or datetime.now(timezone.utc)
    ln = now_utc.astimezone(tz)
    t = (text or "").strip()
    if not t:
        return ParsedTime(FAIL)

    # 1) 相对（带数量的规则优先，避免"一个半小时后"被裸"半小时后"截胡）
    m = _REL_RE.search(t) or _REL_GUO_RE.search(t)
    if m:
        n = _cn2int(m.group(1))
        if n is not None:
            unit, half = m.group(3), bool(m.group(2))
            if unit in ("秒", "秒钟"):
                delta = timedelta(seconds=n)
            elif unit == "分钟":
                delta = timedelta(minutes=n)
            else:
                delta = timedelta(hours=n, minutes=30 if half else 0)
            return _ok(now_utc + delta, ln, tz)
    if _REL_HALF_RE.search(t) or _REL_GUO_HALF_RE.search(t):
        return _ok(now_utc + timedelta(minutes=30), ln, tz)

    # 2) 日（优先级：日词 > 周X > N月N日 > N号）
    day_date = None
    week_based = False
    seg_kind = ""
    for w, off, s in _DAY_WORDS:
        if w in t:
            day_date = (ln + timedelta(days=off)).date()
            seg_kind = s
            break
    if day_date is None:
        m = _WEEK_RE.search(t)
        if m:
            downs = len(m.group(1) or "")
            idx = "一二三四五六".find(m.group(2))
            wd = 6 if idx < 0 else idx        # 日/天 → 6
            monday = ln.date() - timedelta(days=ln.weekday())
            cand = monday + timedelta(days=wd + 7 * downs)
            if downs == 0 and cand < ln.date():
                cand += timedelta(days=7)
            day_date, week_based = cand, True
    if day_date is None:
        m = _MD_RE.search(t)
        if m:
            mo, d = _cn2int(m.group(1)), _cn2int(m.group(2))
            if mo and d:
                y = ln.year + (1 if (mo, d) < (ln.month, ln.day) else 0)
                try:
                    day_date = ln.date().replace(year=y, month=mo, day=d)
                except ValueError:
                    return ParsedTime(FAIL)  # "2月30号"：不落 N号 分支误判，交给 LLM 兜底/追问
    if day_date is None:
        m = _DOM_RE.search(t)
        if m:
            d = _cn2int(m.group(1))
            if d:
                y, mo = ln.year, ln.month
                if d < ln.day:
                    mo += 1
                    if mo > 12:
                        mo, y = 1, y + 1
                try:
                    day_date = ln.date().replace(year=y, month=mo, day=d)
                except ValueError:
                    return ParsedTime(FAIL)  # "31号"于小月：诚实失败不猜下月

    # 3) 段位（独立段位词覆盖/补充日词内嵌段位）
    for w, k in _SEGS:
        if w in t:
            seg_kind = k
            break

    # 4) 时刻
    hour = minute = None
    h24 = False
    m = _HHMM_RE.search(t)
    if m:
        hour, minute, h24 = int(m.group(1)), int(m.group(2)), True
    else:
        m = _CLOCK_RE.search(t)
        if m:
            hour, minute = _cn2int(m.group(1)), 0
            mm = m.group(2) or ""
            if mm == "半":
                minute = 30
            elif mm == "一刻":
                minute = 15
            elif mm == "三刻":
                minute = 45
            elif mm:
                minute = _cn2int(m.group(3)) or 0
            if hour is None or hour > 24:
                hour = None
    if hour is None and seg_kind == "noon":
        hour, minute, h24 = 12, 0, True   # "中午"默认 12:00

    if hour is None:
        return ParsedTime(NEED_TIME) if (day_date is not None or seg_kind) else ParsedTime(FAIL)

    # 5) 段位修正（12h→24h）
    plus_day = 0
    if not h24:
        if seg_kind == "pm" and hour < 12:
            hour += 12
        elif seg_kind == "eve":
            if hour == 12:
                hour, plus_day = 0, 1     # 晚上12点 = 次日 00:00
            elif hour < 12:
                hour += 12
        elif seg_kind == "noon" and hour < 6:
            hour += 12                    # 中午一点 = 13:00
    if hour == 24:
        hour, plus_day = 0, plus_day + 1

    # 6) 组装
    if day_date is not None:
        target = datetime(day_date.year, day_date.month, day_date.day,
                          hour, minute, tzinfo=tz) + timedelta(days=plus_day)
        if week_based and target <= ln:
            target += timedelta(days=7)
        # 显式日（今天/N号/N月N日）已过 → 原样返回，agent 层诚实追问，不偷偷改天
    else:
        target = ln.replace(hour=hour, minute=minute, second=0,
                            microsecond=0) + timedelta(days=plus_day)
        if not h24 and not seg_kind and hour <= 12:
            # 裸 12 小时制：{h, h+12} 取最近未来（10:00 说"八点"→今天 20:00）
            cands = [target]
            if hour + 12 < 24:
                cands.append(target.replace(hour=hour + 12))
            cands.sort()
            target = next((c for c in cands if c > ln), cands[0] + timedelta(days=1))
        elif target <= ln:
            target += timedelta(days=1)   # 段位/24h 时刻今天已过 → 明天（凌晨两点 case）
    return _ok(target.astimezone(timezone.utc), ln, tz)


def strip_time_expressions(text: str) -> str:
    """移除已识别的时间子串（供标题清洗）。重复词形（每天/每周X）先剥，否则残留"每"污染标题。"""
    t = text or ""
    for rx in (_RECUR_WORKDAY_RE, _RECUR_WEEKLY_RE, _RECUR_DAILY_RE,
               _REL_HALF_RE, _REL_GUO_HALF_RE, _REL_RE, _REL_GUO_RE,
               _MD_RE, _DOM_RE, _WEEK_RE, _HHMM_RE, _CLOCK_RE):
        t = rx.sub("", t)
    for w, _, _ in _DAY_WORDS:
        t = t.replace(w, "")
    for w, _ in _SEGS:
        t = t.replace(w, "")
    return t.strip(" ，。,、的")


# ── 重复规则（P1a）：解析 / 展示 / 首触发对齐 / 触发后滚动 ──
_RECUR_WORKDAY_RE = re.compile(r"每个?工作日")
_RECUR_DAILY_RE = re.compile(r"每天|每日|天天")
_RECUR_WEEKLY_RE = re.compile(r"每个?(?:周|星期|礼拜)([一二三四五六日天])")


def parse_recur(text: str) -> str:
    """重复词形 → 'daily' | 'workday' | 'weekly:1..7' | ''（无重复）。"""
    t = text or ""
    if _RECUR_WORKDAY_RE.search(t):
        return "workday"
    m = _RECUR_WEEKLY_RE.search(t)
    if m:
        idx = "一二三四五六".find(m.group(1))
        return f"weekly:{7 if idx < 0 else idx + 1}"
    if _RECUR_DAILY_RE.search(t):
        return "daily"
    return ""


def recur_label(recur: str) -> str:
    """'daily'→每天 / 'workday'→工作日 / 'weekly:3'→每周三；供话术与卡片展示。"""
    if recur == "daily":
        return "每天"
    if recur == "workday":
        return "工作日"
    if recur.startswith("weekly:"):
        try:
            return f"每周{'一二三四五六日'[int(recur.split(':')[1]) - 1]}"
        except Exception:
            return "每周"
    return ""


# 跨域提醒 P1c：事件锚定的提前量（「提前10分钟/开赛前半小时」）；无词形用默认
_LEAD_HALF_RE = re.compile(r"(?:提前|开赛前|开始前|前)\s*半\s*个?\s*(?:小时|钟头)")
_LEAD_RE = re.compile(r"(?:提前|开赛前|开始前|前)\s*" + _NUM + r"\s*个?\s*(分钟|小时|钟头)")


def parse_lead(text: str, default_s: int = 600) -> int:
    """事件提前量（秒）：「提前N分钟/开赛前半小时/前一小时」；无词形返回 default_s。"""
    t = text or ""
    if _LEAD_HALF_RE.search(t):
        return 1800
    m = _LEAD_RE.search(t)
    if m:
        n = _cn2int(m.group(1))
        if n:
            return n * (60 if m.group(2) == "分钟" else 3600)
    return default_s


def align_workday(fire_at: int, tz: tzinfo | None = None) -> int:
    """工作日系列首触发落在周末 → 顺延到下周一同时刻。"""
    tz = tz or business_tz()
    dt = datetime.fromtimestamp(fire_at, tz)
    while dt.weekday() >= 5:
        dt += timedelta(days=1)
    return int(dt.timestamp())


def next_recur_fire(recur: str, prev_fire: int, now_ts: int,
                    tz: tzinfo | None = None) -> int:
    """触发后滚动到下一次（恒 > now：停机错过的次数直接跳过，不补发轰炸）。
    中国无夏令时，按本地墙钟推进天/周步长安全。"""
    tz = tz or business_tz()
    step = timedelta(days=7) if recur.startswith("weekly") else timedelta(days=1)
    dt = datetime.fromtimestamp(prev_fire, tz)
    while True:
        dt += step
        if recur == "workday":
            while dt.weekday() >= 5:
                dt += timedelta(days=1)
        ts = int(dt.timestamp())
        if ts > now_ts:
            return ts
