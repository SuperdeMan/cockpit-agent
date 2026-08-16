"""中文时间词——**全仓唯一声明**（时段词 / 日词 / 中文数字 / 12h→24h 修正）。

## 为什么需要它

这一族在本仓有过**两份各自演化**的实现：

- `agents/reminder/src/timeparse.py`：完整的「中文时间表达 → epoch」，段位词表 9 个词；
- `agents/_sdk/timewindow.py`：G1 时限/用餐窗要的「时刻 → epoch」，段位词表 **7 个词**。

两份都「各自正确」，直到你把同一句话喂给它们（2026-08-16 实测，`now=09:00`）：

| 输入 | timeparse | timewindow |
|---|---|---|
| 早晨八点 | 明天 08:00 | **今天 20:00** ← 词表少了「早晨」，退化成裸 12 小时制消歧 |
| 晚上12点 | 明天 00:00 | **今天 12:00** ← 修正规则少了「晚上12点=次日零点」这一支 |

判据是 §4.3 那条已经应验过三次的：**同一件事有两份各自正确的实现，
就迟早会有一份是错的**（`runtime/clock.py` 的开头写的是同一句话，那次栽在业务时区上）。
所以词表与修正规则收敛到这里，**两边的匹配策略仍归各自**——timeparse 在整句里找，
timewindow 只认紧贴时刻的前缀，那是两个不同的问题，不该一起收。

## 落在 `runtime/` 而不是 `agents/_sdk/`

同 `runtime/polarity.py` 的理由，先查的是镜像依赖闭包：`orchestrator/cloud/Dockerfile`
与 `agents/reminder/Dockerfile` 都 `COPY runtime`，而云侧编排镜像里没有 `agents/`
（`runtime/slot_fidelity.py` 的原话回查要用同一份词表）。
B3 那次「代码里 import 得到 ≠ 镜像里拷进去了」在 40 小时里毫无症状。
"""
from __future__ import annotations

# ── 中文数字 ──────────────────────────────────────────────────────────
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
#: 中文数字字符集（正则字符类用）。**只此一份**——它此前在 timeparse 的 `_NUM`
#: 与 timewindow 的 `_CLOCK_RE` 里各写了一遍。
CN_NUM_CHARS = "一两二三四五六七八九十"
#: 数字/中文数字的**词法**（非捕获）。需要捕获的调用方自己包一层圆括号——
#: 包出来的 `group(1)` 语义不变，而词表仍然只有这一份。
CN_NUM_SRC = rf"(?:\d+|[{CN_NUM_CHARS}]+)"


def cn_int(s: str | None) -> int | None:
    """「三」「十」「十二」「二十三」「15」→ int；认不出返回 None（**不猜**）。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        tens, _, ones = s.partition("十")
        t = 1 if tens == "" else CN_DIGITS.get(tens)
        o = 0 if ones == "" else CN_DIGITS.get(ones)
        return None if t is None or o is None else t * 10 + o
    return CN_DIGITS.get(s) if len(s) == 1 else None


# ── 段位词（一天里的时段）──────────────────────────────────────────────
#: (词, kind)。**顺序即优先级**：调用方按序取第一个命中的，改顺序会改行为。
#: kind 只有五个：dawn / am / noon / pm / eve。
SEGMENTS: tuple[tuple[str, str], ...] = (
    ("凌晨", "dawn"), ("早上", "am"), ("早晨", "am"), ("上午", "am"),
    ("中午", "noon"), ("下午", "pm"), ("傍晚", "pm"), ("晚上", "eve"),
    ("夜里", "eve"),
)
#: 段位默认时刻——「明天下午」这种「日+段位、没时刻」按惯例默成单。
SEG_DEFAULT_HOUR = {"dawn": 6, "am": 8, "noon": 12, "pm": 15, "eve": 20}
#: 正则用的段位词 alternation（长度相同、无前缀吞并问题）。
SEG_ALT = "|".join(w for w, _ in SEGMENTS)

# ── 日词 ──────────────────────────────────────────────────────────────
#: (词, 天偏移, 内嵌段位)。**长词在前**——「大后天」含「后天」、
#: `the day after tomorrow` 含 `tomorrow`，顺序不能动。
#:
#: ⚠ **英文日词在同一张表里，不另起一张**（2026-08-16，Q12）：QA 轮 I-041
#: 「Shenzhen weather **tomorrow**」被答成当前实况，根因就是 `weather.py` 自带的
#: **第三份**日词表只有中文。分成中英两张表只会让下一个消费方挑错一张。
#: 匹配一律按小写扫（`day_offset_of`），中文 `.lower()` 是恒等变换。
DAY_WORDS: tuple[tuple[str, int, str], ...] = (
    ("大后天", 3, ""), ("后天", 2, ""), ("明早", 1, "am"), ("明晚", 1, "eve"),
    ("明天", 1, ""), ("明日", 1, ""), ("今晚", 0, "eve"), ("今天", 0, ""),
    ("the day after tomorrow", 2, ""), ("tomorrow night", 1, "eve"),
    ("tomorrow morning", 1, "am"), ("tomorrow", 1, ""),
    ("tonight", 0, "eve"), ("today", 0, ""),
)
DAY_ALT = "|".join(w for w, _, _ in DAY_WORDS)


def day_offset_of(text: str | None) -> int | None:
    """整句里第一个日词的天偏移；没有返回 None（**不回落 0**）。

    「认不出」和「说的是今天」必须分得开——weather 此前把两者合成 `return 0`，
    于是英文时间词一律被当成「问的是现在」。
    """
    t = (text or "").lower()
    return next((off for w, off, _ in DAY_WORDS if w in t), None)

#: 时刻的**扫描**词法（非捕获）：`HH:MM` 或 `N点[半|一刻|三刻|N分]`。
#: 需要分组取值的调用方（timeparse）自己写带捕获的版本——
#: 两者必须接受同一批字符串，`runtime/tests/test_cntime.py` 拿样本逐条比对。
CLOCK_SRC = (r"(?:(?:[01]?\d|2[0-3])[:：][0-5]\d"
             rf"|{CN_NUM_SRC}\s*点(?:\s*(?:半|一刻|三刻|{CN_NUM_SRC}\s*分?))?)")


def segment_of(text: str | None) -> str:
    """整句里第一个段位词的 kind；没有返回 ""。"""
    t = text or ""
    for word, kind in SEGMENTS:
        if word in t:
            return kind
    return ""


def segment_kind(word: str | None) -> str:
    """段位词本身 → kind（timewindow 那种「只认紧贴前缀」的调用方用）。"""
    w = (word or "").strip()
    return next((k for s, k in SEGMENTS if s == w), "")


def to_24h(hour: int, seg_kind: str) -> tuple[int, int]:
    """12 小时制时刻 + 段位 → (24 小时制时刻, 跨天数)。**修正规则的唯一实现**。

    ⚠ 这里刻意**不看**「原话是不是 24 小时制写法」：`晚上5:00` 里段位词明摆着，
    修正就该生效。timeparse 此前用 `h24` 把这一支跳过去了（于是 `晚上12:00`
    留在中午），timewindow 则一直是对的——收敛取的是对的那一版。
    """
    plus_day = 0
    if seg_kind == "pm" and hour < 12:
        hour += 12
    elif seg_kind == "eve":
        if hour == 12:
            hour, plus_day = 0, 1          # 晚上12点 = 次日 00:00
        elif hour < 12:
            hour += 12
    elif seg_kind == "noon" and hour < 6:
        hour += 12                          # 中午一点 = 13:00
    if hour == 24:
        hour, plus_day = 0, plus_day + 1
    return hour, plus_day
