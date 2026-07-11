# 智能提醒 Agent P0 实施计划（可直接接手开工版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 `docs/design/2026-07-11-reminder-agent-design.md`（已批准，含 D7）的 P0：自然语言创建日程提醒/待办 + 到点 NATS proactive 触达 + HMI 卡片与右舞台 agenda 双形态。

**Architecture:** 独立 `reminder` Agent（50074，first_party/core），自有 PG 表 `reminder_item`（asyncpg，无 PG 内存降级）+ 进程内 asyncio 调度轮询原子领取 → 复用 `agent.proactive`→edge-gateway→HMI 链路；**不改 proto / 编排核心 / 网关**。

**Tech Stack:** Python 3.11（agents/_sdk BaseAgent、asyncpg、nats-py）、React+TS（types.ts 契约 + Aurora Glass）、纯逻辑 `.mjs`（node:test）。

**实施者必读（本仓惯例，违者返工）：**
1. 工作目录=仓库根；先 `make proto`（或 `scripts/gen-proto.ps1`）确保 `gen/` 存在。
2. **依赖闭包**：`nats-py` 已在 `agents/_sdk/requirements.txt`；`asyncpg` 不在——本计划经 `agents/reminder/requirements.txt` 叠加安装（Task 4），**不动** `_sdk/requirements.txt`（避免重建全部 Agent 镜像）。
3. Docker 无卷挂载：改源码后必须 `docker compose -f compose.yaml up -d --build reminder-agent`（`--force-recreate` 不够）。
4. 文件一律 UTF-8；PowerShell 写文件注意编码陷阱（用 Write/Edit 工具，不用 `Out-File`）。
5. 提交在 main、**不 push**（push 是泓舟红线，需明示）。
6. 全部新 Python 文件跑 `python -m py_compile <file>` 再跑测试。

---

## 文件结构（先定边界再动手）

```
agents/reminder/
  manifest.yaml            # 能力/权限/route_hints 声明（唯一路由入口，不改编排）
  main.py                  # serve(ReminderAgent()) 入口
  Dockerfile               # 仿 deep_research + 叠加 requirements.txt
  requirements.txt         # asyncpg>=0.29（叠加在 _sdk 之上）
  schema.sql               # reminder_item 表（幂等）
  src/__init__.py
  src/timeparse.py         # 纯函数：中文时间表达→epoch（注入 now/tz 可测）
  src/store.py             # ReminderStore：单类双后端（PG/内存），claim_due 原子领取
  src/scheduler.py         # ReminderScheduler：tick/run_forever，publish 注入
  src/agent.py             # ReminderAgent：4 intent handler + on_start 起调度
  tests/{__init__.py,test_timeparse.py,test_store.py,test_scheduler.py,test_agent.py}
agents/_sdk/shared_state.py          # +REMINDERS_ACTIVE +REMINDER_PENDING
hmi/src/types.ts                     # +ReminderItem/ReminderListCard/ReminderCard + union + catalog
hmi/src/reminderStage.mjs            # 纯函数：resolveView/groupByDay/timelineWindow（node 可测）
hmi/src/reminderStage.test.mjs
hmi/src/components/Cards.tsx         # +ReminderListCardView/+ReminderCardView + 2 case
hmi/src/components/ContextualStage.tsx  # deriveScene agenda 分支 + AgendaStage 双形态
deploy/docker-compose.yaml           # reminder-agent 服务块（50074）
test/e2e_reminder.py                 # 真栈闭环（WS 创建→NATS 收 fired→列表/完成→清空确认）
test/eval_corpus/route_hints_cases.yaml  # 提醒正反例
docs/conventions.md / docs/design/README.md / AGENTS.md / .env.example  # 登记（Task 9）
```

责任边界：`timeparse` 不碰存储；`store` 不碰 NATS；`scheduler` 只依赖 store+publish 可调用；`agent` 只做编排与话术。HMI 的分组/取窗纯逻辑全在 `reminderStage.mjs`（node 可测），TSX 只渲染。

---

## Task 0：基线检查

- [ ] **Step 0.1** 运行基线，记录当前 passed 数（后续零回归对照）：

```bash
python -m pytest --import-mode=importlib -q 2>&1 | tail -3
cd hmi && npm test 2>&1 | tail -3 && cd ..
```

预期：与 `AGENTS.md` §4 当前口径一致（约 1200 passed / node 127+）。不绿先修环境再开工。

---

## Task 1：`timeparse.py`（TDD，先测后码）

**Files:**
- Create: `agents/reminder/src/__init__.py`（空文件）、`agents/reminder/tests/__init__.py`（空文件）
- Test: `agents/reminder/tests/test_timeparse.py`
- Create: `agents/reminder/src/timeparse.py`

**解析语义（黄金规则，测试即规格）：**
- 相对：`N秒后/N分钟后/半小时后/一个半小时后/N小时后`（中文数字与阿拉伯均收）→ now+delta。
- 日词：`今天/今晚/明天/明早/明晚/后天/大后天/周X/下周X/N月N日/N号`。
- 段位：`凌晨/早上/早晨/上午/中午/下午/傍晚/晚上/夜里`；`中午` 无时刻默认 12:00，其余段位无时刻→`need_time`。
- 时刻：`HH:MM` / `N点[半|一刻|三刻|N分]`；段位修正（下午三点→15:00；晚上12点→次日 00:00；中午一点→13:00）。
- **裸 12 小时制时刻**（无日无段位）：在 {h, h+12} 里取"最近的未来"（10:00 说"八点"→今天 20:00；说"两点半"→今天 14:30）。
- 显式日（今天/N号）+ 已过时刻 → 原样返回过去时刻（由 agent 层诚实追问，不偷偷改天）；周X 候选已过 → +7 天。
- 只有日/段位没时刻 → `need_time`（D5 追问）；什么都没识别 → `fail`（agent 走 LLM 兜底）。
- 时区：`REMINDER_TZ`（默认 Asia/Shanghai）经 zoneinfo；不可用回退**固定 UTC+8**（中国无夏令时，恒正确；Windows 宿主无 tzdata 也能跑测试）。

- [ ] **Step 1.1 写失败测试**（`agents/reminder/tests/test_timeparse.py`，完整文件）：

```python
"""timeparse 黄金用例：测试即规格。now 固定 2026-07-11(周六) 10:00 +8。"""
from datetime import datetime, timedelta, timezone

import pytest

from agents.reminder.src.timeparse import (
    OK, NEED_TIME, FAIL, parse_time_text, strip_time_expressions, format_display)

TZ = timezone(timedelta(hours=8), name="UTC+8")
NOW_LOCAL = datetime(2026, 7, 11, 10, 0, tzinfo=TZ)   # 周六
NOW = NOW_LOCAL.astimezone(timezone.utc)


def L(*a):
    return datetime(*a, tzinfo=TZ)


def ts(dt):
    return int(dt.timestamp())


def P(text):
    return parse_time_text(text, now=NOW, tz=TZ)


# ── 相对时间 ──
@pytest.mark.parametrize("text,delta", [
    ("20秒后提醒我测试", timedelta(seconds=20)),
    ("5分钟后提醒我", timedelta(minutes=5)),
    ("十分钟后叫我", timedelta(minutes=10)),
    ("半小时后提醒我给客户回电话", timedelta(minutes=30)),
    ("一个半小时后提醒我", timedelta(minutes=90)),
    ("两小时后提醒我", timedelta(hours=2)),
    ("1个小时之后提醒我", timedelta(hours=1)),
    ("45分钟以后叫我", timedelta(minutes=45)),
])
def test_relative(text, delta):
    r = P(text)
    assert r.status == OK
    assert r.fire_at == ts(NOW + delta)


# ── 绝对：日 + 段位 + 时刻 ──
@pytest.mark.parametrize("text,local", [
    ("明天早上八点提醒我带充电线", L(2026, 7, 12, 8, 0)),
    ("明天早上8点", L(2026, 7, 12, 8, 0)),
    ("明早八点叫我", L(2026, 7, 12, 8, 0)),
    ("明天下午三点半提醒我", L(2026, 7, 12, 15, 30)),
    ("明天 9:15 提醒我开会", L(2026, 7, 12, 9, 15)),
    ("明晚八点提醒我", L(2026, 7, 12, 20, 0)),
    ("后天中午提醒我吃药", L(2026, 7, 13, 12, 0)),
    ("后天中午一点提醒我", L(2026, 7, 13, 13, 0)),
    ("大后天晚上八点提醒我", L(2026, 7, 14, 20, 0)),
    ("今晚八点提醒我", L(2026, 7, 11, 20, 0)),
    ("今天下午四点提醒我取快递", L(2026, 7, 11, 16, 0)),
    ("晚上八点提醒我", L(2026, 7, 11, 20, 0)),        # 无日词：今天晚上
    ("下午三点提醒我", L(2026, 7, 11, 15, 0)),
    ("15:30提醒我", L(2026, 7, 11, 15, 30)),
    ("中午提醒我吃饭", L(2026, 7, 11, 12, 0)),          # 中午默认 12:00
    ("凌晨两点提醒我", L(2026, 7, 12, 2, 0)),           # 今天 02:00 已过 → 明天
    ("晚上12点提醒我", L(2026, 7, 12, 0, 0)),           # 晚上12点 = 次日 00:00
])
def test_absolute(text, local):
    r = P(text)
    assert r.status == OK, f"{text} -> {r.status}"
    assert r.fire_at == ts(local), f"{text}: got {r.fire_at}, want {ts(local)}"


# ── 裸 12 小时制：{h, h+12} 取最近未来 ──
@pytest.mark.parametrize("text,local", [
    ("八点提醒我", L(2026, 7, 11, 20, 0)),      # 08:00 已过 → 今天 20:00
    ("两点半提醒我", L(2026, 7, 11, 14, 30)),   # 02:30 已过 → 今天 14:30
    ("十一点提醒我", L(2026, 7, 11, 11, 0)),    # 11:00 未过 → 今天 11:00
    ("3点一刻提醒我", L(2026, 7, 11, 15, 15)),
])
def test_bare_clock_future_nearest(text, local):
    r = P(text)
    assert r.status == OK
    assert r.fire_at == ts(local)


# ── 周 / 日期 ──
@pytest.mark.parametrize("text,local", [
    ("周五下午三点提醒我", L(2026, 7, 17, 15, 0)),      # 本周五已过 → 下周五
    ("周日晚上七点提醒我", L(2026, 7, 12, 19, 0)),      # 本周日=明天
    ("下周三上午十点提醒我", L(2026, 7, 15, 10, 0)),
    ("7月20号早上九点提醒我", L(2026, 7, 20, 9, 0)),
    ("8月1日下午两点提醒我", L(2026, 8, 1, 14, 0)),
    ("3月5号早上八点提醒我", L(2027, 3, 5, 8, 0)),      # 今年已过 → 明年
    ("25号下午四点提醒我", L(2026, 7, 25, 16, 0)),      # 本月 25 未过
    ("5号早上八点提醒我", L(2026, 8, 5, 8, 0)),         # 本月 5 号已过 → 下月
])
def test_week_and_date(text, local):
    r = P(text)
    assert r.status == OK
    assert r.fire_at == ts(local)


# ── need_time / fail / 显式过去 ──
def test_day_without_clock_needs_time():
    for t in ("明天提醒我开会", "周三提醒我", "明天早上提醒我", "下午提醒我"):
        assert P(t).status == NEED_TIME, t


def test_unparseable_fails():
    for t in ("饭点提醒我", "到公司提醒我拿文件", ""):
        assert P(t).status == FAIL, t


def test_explicit_today_past_kept_as_is():
    r = P("今天九点提醒我")   # now 10:00，九点已过；显式"今天"不偷偷改天
    assert r.status == OK
    assert r.fire_at == ts(L(2026, 7, 11, 9, 0))
    assert r.fire_at < ts(NOW_LOCAL)


# ── display / strip ──
def test_display_readable():
    assert "明天" in P("明天早上八点提醒我").display
    assert "08:00" in P("明天早上八点提醒我").display
    assert "今天" in P("下午三点提醒我").display


def test_format_display_roundtrip():
    fire = ts(L(2026, 7, 12, 8, 0))
    assert format_display(fire, now=NOW, tz=TZ) == "明天 08:00"


def test_strip_time_expressions():
    assert strip_time_expressions("明天早上八点提醒我带充电线") == "提醒我带充电线"
    assert strip_time_expressions("半小时后提醒我给客户回电话") == "提醒我给客户回电话"
    assert strip_time_expressions("提醒我周五下午三点去接孩子") == "提醒我去接孩子"
```

- [ ] **Step 1.2 跑测确认失败**：

```bash
python -m pytest agents/reminder/tests/test_timeparse.py -q
```

预期：`ModuleNotFoundError: agents.reminder.src.timeparse`（收集期报错即为"失败"）。

- [ ] **Step 1.3 实现 `agents/reminder/src/timeparse.py`**（完整文件）：

```python
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

    # 1) 相对
    if _REL_HALF_RE.search(t):
        return _ok(now_utc + timedelta(minutes=30), ln, tz)
    m = _REL_RE.search(t)
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
                day_date = ln.date().replace(year=y, month=mo, day=d)
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
                day_date = ln.date().replace(year=y, month=mo, day=d)

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
    """移除已识别的时间子串（供标题清洗）。"""
    t = text or ""
    for rx in (_REL_HALF_RE, _REL_RE, _MD_RE, _DOM_RE, _WEEK_RE, _HHMM_RE, _CLOCK_RE):
        t = rx.sub("", t)
    for w, _, _ in _DAY_WORDS:
        t = t.replace(w, "")
    for w, _ in _SEGS:
        t = t.replace(w, "")
    return t.strip(" ，。,、的")
```

- [ ] **Step 1.4 跑测通过**：

```bash
python -m py_compile agents/reminder/src/timeparse.py
python -m pytest agents/reminder/tests/test_timeparse.py -q
```

预期：**约 40 用例全 PASS**。任何 fail 先怀疑规则实现、不改测试预期（测试即规格；确需改语义须在设计文档 §7 同步）。

- [ ] **Step 1.5 提交**：

```bash
git add agents/reminder/src agents/reminder/tests
git commit -m "feat(reminder): 中文时间解析 timeparse（确定性规则+固定时区回退，黄金用例 40）"
```

---

## Task 2：`store.py`（单类双后端，内存分支 TDD；PG 分支 e2e 覆盖）

**Files:**
- Test: `agents/reminder/tests/test_store.py`
- Create: `agents/reminder/src/store.py`、`agents/reminder/schema.sql`

- [ ] **Step 2.1 写失败测试**（`agents/reminder/tests/test_store.py`，完整文件）：

```python
"""ReminderStore 内存分支语义（PG 分支由 test/e2e_reminder.py 真栈覆盖）。"""
import pytest

from agents.reminder.src.store import Reminder, ReminderStore


async def _store() -> ReminderStore:
    s = ReminderStore(dsn="")   # 强制内存分支
    await s.init()
    return s


@pytest.mark.asyncio
async def test_add_and_list_ordering():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="B", kind="time", fire_at=2000))
    await s.add(Reminder(user_id="u1", title="A", kind="time", fire_at=1000))
    await s.add(Reminder(user_id="u1", title="T", kind="todo"))
    times, todos = await s.list_split("u1")
    assert [r.title for r in times] == ["A", "B"]      # fire_at 升序
    assert [r.title for r in todos] == ["T"]


@pytest.mark.asyncio
async def test_list_range_filters():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="今早", kind="time", fire_at=1000))
    await s.add(Reminder(user_id="u1", title="下周", kind="time", fire_at=9000))
    times, _ = await s.list_split("u1", from_ts=0, to_ts=5000)
    assert [r.title for r in times] == ["今早"]


@pytest.mark.asyncio
async def test_claim_due_atomic_and_cross_user():
    s = await _store()
    r1 = await s.add(Reminder(user_id="u1", title="X", kind="time", fire_at=100))
    await s.add(Reminder(user_id="u2", title="Y", kind="time", fire_at=100))
    due1 = await s.claim_due(200)
    due2 = await s.claim_due(200)                       # 二次领取必须为空（防重复触发）
    assert sorted(d.title for d in due1) == ["X", "Y"]  # 跨用户
    assert all(d.status == "fired" for d in due1)
    assert due2 == []
    assert (await s.get("u1", r1.id)).status == "fired"


@pytest.mark.asyncio
async def test_todo_and_future_never_claimed():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="T", kind="todo"))
    await s.add(Reminder(user_id="u1", title="F", kind="time", fire_at=10 ** 12))
    assert await s.claim_due(10 ** 9) == []


@pytest.mark.asyncio
async def test_find_by_title_and_set_status():
    s = await _store()
    r = await s.add(Reminder(user_id="u1", title="买牛奶", kind="time", fire_at=1000))
    assert [h.id for h in await s.find_by_title("u1", "牛奶")] == [r.id]
    assert await s.set_status("u1", r.id, "done")
    assert (await s.get("u1", r.id)).status == "done"
    assert await s.find_by_title("u1", "牛奶") == []    # done 不入默认过滤
    assert not await s.set_status("u1", "no-such-id", "done")


@pytest.mark.asyncio
async def test_cancel_all_scoped_to_user():
    s = await _store()
    await s.add(Reminder(user_id="u1", title="A", kind="time", fire_at=1000))
    await s.add(Reminder(user_id="u1", title="B", kind="todo"))
    await s.add(Reminder(user_id="u2", title="C", kind="time", fire_at=1000))
    assert await s.cancel_all("u1") == 2
    times, todos = await s.list_split("u1")
    assert times == [] and todos == []
    times2, _ = await s.list_split("u2")
    assert len(times2) == 1


def test_to_card_item_contract():
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 11, 10, 0, tzinfo=tz).astimezone(timezone.utc)
    fire = int(datetime(2026, 7, 12, 8, 0, tzinfo=tz).timestamp())
    r = Reminder(id="rid1", user_id="u1", title="带充电线", kind="time",
                 fire_at=fire, status="pending")
    item = r.to_card_item(now=now, tz=tz)
    assert item == {"id": "rid1", "title": "带充电线", "kind": "time",
                    "status": "pending", "time_display": "明天 08:00",
                    "fire_at_ms": fire * 1000}
    todo = Reminder(id="rid2", user_id="u1", title="买牛奶", kind="todo")
    assert todo.to_card_item(now=now, tz=tz)["time_display"] == ""
```

- [ ] **Step 2.2 跑测确认失败**：`python -m pytest agents/reminder/tests/test_store.py -q` → 收集期 `ModuleNotFoundError`。

- [ ] **Step 2.3 写 `agents/reminder/schema.sql`**（完整文件，与设计 §5 一致）：

```sql
-- reminder_item：与 registry/memory 同一 PG 实例、独立表（启动幂等建表）
CREATE TABLE IF NOT EXISTS reminder_item (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL,
  vehicle_id  TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'time',
  fire_at     BIGINT NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending',
  created_at  BIGINT NOT NULL,
  fired_at    BIGINT NOT NULL DEFAULT 0,
  source      TEXT NOT NULL DEFAULT 'user',
  recur       TEXT NOT NULL DEFAULT '',
  extra       JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_reminder_due ON reminder_item (status, fire_at);
CREATE INDEX IF NOT EXISTS idx_reminder_user ON reminder_item (user_id, status);
```

- [ ] **Step 2.4 实现 `agents/reminder/src/store.py`**（完整文件）：

```python
"""提醒持久层：PG（asyncpg，同 PG 实例独立表）优先，无 PG 内存兜底（诚实降级）。

仿 memory/pg_store.py 的单类双后端形态；claim_due 用 UPDATE…RETURNING 原子领取，
重复触发/未来多实例安全。内存分支重启丢失——init 时打 WARNING。
"""
from __future__ import annotations
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo

from .timeparse import business_tz, format_display

logger = logging.getLogger("agent.reminder.store")

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")
PENDING, FIRED, DONE, CANCELLED = "pending", "fired", "done", "cancelled"
ACTIVE = (PENDING, FIRED)     # 默认过滤：用户可见/可操作态


@dataclass
class Reminder:
    user_id: str
    title: str
    kind: str = "time"                 # time | todo
    fire_at: int = 0                   # epoch 秒（UTC）；todo 恒 0
    status: str = PENDING
    id: str = ""
    vehicle_id: str = ""
    created_at: int = 0
    fired_at: int = 0
    source: str = "user"
    recur: str = ""
    extra: dict = field(default_factory=dict)

    def to_card_item(self, *, now: datetime | None = None,
                     tz: tzinfo | None = None) -> dict:
        """ReminderItem 卡片契约（设计 §9.1）。time_display 后端本地化，HMI 不做时区运算。"""
        item = {"id": self.id, "title": self.title, "kind": self.kind,
                "status": self.status,
                "time_display": format_display(self.fire_at, now=now, tz=tz)
                if self.fire_at else ""}
        if self.fire_at:
            item["fire_at_ms"] = self.fire_at * 1000
        return item


class ReminderStore:
    def __init__(self, dsn: str | None = None):
        self._dsn = os.getenv("POSTGRES_DSN", "") if dsn is None else dsn
        self._pool = None
        self._pg_ok = False
        self._mem: dict[str, Reminder] = {}   # id -> Reminder（PG 不可用兜底）

    @property
    def pg_ok(self) -> bool:
        return self._pg_ok

    async def init(self) -> bool:
        if not self._dsn:
            logger.warning("ReminderStore: 无 POSTGRES_DSN，内存态兜底（重启丢失提醒）")
            return False
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
            with open(_SCHEMA_PATH, encoding="utf-8") as f:
                schema = f.read()
            async with self._pool.acquire() as conn:
                await conn.execute(schema)
            self._pg_ok = True
            logger.info("ReminderStore: PG 就绪（reminder_item）")
        except Exception as e:
            logger.warning("ReminderStore: PG 不可用（%s），内存态兜底（重启丢失提醒）", e)
            self._pg_ok = False
        return self._pg_ok

    # ── 写入 ──
    async def add(self, r: Reminder) -> Reminder:
        r.id = r.id or uuid.uuid4().hex
        r.created_at = r.created_at or int(time.time())
        if self._pg_ok:
            import json
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO reminder_item (id,user_id,vehicle_id,title,kind,fire_at,"
                    "status,created_at,fired_at,source,recur,extra) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)",
                    r.id, r.user_id, r.vehicle_id, r.title, r.kind, r.fire_at,
                    r.status, r.created_at, r.fired_at, r.source, r.recur,
                    json.dumps(r.extra, ensure_ascii=False))
        else:
            self._mem[r.id] = r
        return r

    # ── 读取 ──
    async def get(self, user_id: str, rid: str) -> Reminder | None:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM reminder_item WHERE id=$1 AND user_id=$2", rid, user_id)
            return self._row(row) if row else None
        r = self._mem.get(rid)
        return r if r and r.user_id == user_id else None

    async def list_split(self, user_id: str, *, from_ts: int = 0, to_ts: int = 0,
                         statuses: tuple = ACTIVE,
                         limit: int = 50) -> tuple[list[Reminder], list[Reminder]]:
        """(定时项按 fire_at 升序, 待办按 created_at 升序)。to_ts=0 表示无上界。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                trs = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND kind='time' "
                    "AND status=ANY($2) AND fire_at>=$3 AND ($4=0 OR fire_at<$4) "
                    "ORDER BY fire_at ASC LIMIT $5",
                    user_id, list(statuses), from_ts, to_ts, limit)
                tds = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND kind='todo' "
                    "AND status=ANY($2) ORDER BY created_at ASC LIMIT $3",
                    user_id, list(statuses), limit)
            return [self._row(x) for x in trs], [self._row(x) for x in tds]
        rs = [r for r in self._mem.values() if r.user_id == user_id and r.status in statuses]
        times = sorted((r for r in rs if r.kind == "time"
                        and r.fire_at >= from_ts and (to_ts == 0 or r.fire_at < to_ts)),
                       key=lambda r: r.fire_at)[:limit]
        todos = sorted((r for r in rs if r.kind == "todo"),
                       key=lambda r: r.created_at)[:limit]
        return times, todos

    async def find_by_title(self, user_id: str, q: str,
                            statuses: tuple = ACTIVE) -> list[Reminder]:
        q = (q or "").strip()
        if not q:
            return []
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM reminder_item WHERE user_id=$1 AND status=ANY($2) "
                    "AND title LIKE $3 ORDER BY fire_at ASC", user_id, list(statuses),
                    f"%{q}%")
            return [self._row(x) for x in rows]
        return sorted((r for r in self._mem.values() if r.user_id == user_id
                       and r.status in statuses and q in r.title),
                      key=lambda r: r.fire_at)

    # ── 状态转移 ──
    async def set_status(self, user_id: str, rid: str, status: str) -> bool:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET status=$1 WHERE id=$2 AND user_id=$3",
                    status, rid, user_id)
            return tag.endswith("1")
        r = self._mem.get(rid)
        if not r or r.user_id != user_id:
            return False
        r.status = status
        return True

    async def cancel_all(self, user_id: str) -> int:
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                tag = await conn.execute(
                    "UPDATE reminder_item SET status='cancelled' "
                    "WHERE user_id=$1 AND status=ANY($2)", user_id, list(ACTIVE))
            try:
                return int(tag.split()[-1])
            except Exception:
                return 0
        n = 0
        for r in self._mem.values():
            if r.user_id == user_id and r.status in ACTIVE:
                r.status = CANCELLED
                n += 1
        return n

    async def claim_due(self, now_ts: int) -> list[Reminder]:
        """原子领取到期项（pending→fired，跨用户）。二次调用不重复返回。"""
        if self._pg_ok:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "UPDATE reminder_item SET status='fired', fired_at=$1 "
                    "WHERE status='pending' AND kind='time' AND fire_at>0 "
                    "AND fire_at<=$1 RETURNING *", now_ts)
            return [self._row(x) for x in rows]
        due = []
        for r in self._mem.values():
            if r.status == PENDING and r.kind == "time" and 0 < r.fire_at <= now_ts:
                r.status, r.fired_at = FIRED, now_ts
                due.append(r)
        return sorted(due, key=lambda r: r.fire_at)

    @staticmethod
    def _row(row) -> Reminder:
        import json
        extra = row["extra"]
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        return Reminder(id=row["id"], user_id=row["user_id"], vehicle_id=row["vehicle_id"],
                        title=row["title"], kind=row["kind"], fire_at=row["fire_at"],
                        status=row["status"], created_at=row["created_at"],
                        fired_at=row["fired_at"], source=row["source"],
                        recur=row["recur"], extra=extra or {})
```

- [ ] **Step 2.5 跑测通过**：

```bash
python -m py_compile agents/reminder/src/store.py
python -m pytest agents/reminder/tests/test_store.py -q
```

预期：8 PASS（内存分支；宿主无 asyncpg 也能跑——PG 分支只在 init 成功后走）。

- [ ] **Step 2.6 提交**：

```bash
git add agents/reminder/src/store.py agents/reminder/schema.sql agents/reminder/tests/test_store.py
git commit -m "feat(reminder): ReminderStore 单类双后端（PG asyncpg/内存降级）+ claim_due 原子领取"
```

---

## Task 3：`scheduler.py`（假 store/publisher/时钟 TDD）

**Files:**
- Test: `agents/reminder/tests/test_scheduler.py`
- Create: `agents/reminder/src/scheduler.py`

- [ ] **Step 3.1 写失败测试**（完整文件）：

```python
"""调度器 tick 语义：领取→合并播报→publish 一次；异常不炸循环。"""
import pytest

from agents.reminder.src.scheduler import ReminderScheduler
from agents.reminder.src.store import Reminder, ReminderStore


class Pub:
    def __init__(self, fail: bool = False):
        self.sent, self.fail = [], fail

    async def __call__(self, payload: dict):
        if self.fail:
            raise RuntimeError("nats down")
        self.sent.append(payload)


async def _store_with(*reminders) -> ReminderStore:
    s = ReminderStore(dsn="")
    await s.init()
    for r in reminders:
        await s.add(r)
    return s


@pytest.mark.asyncio
async def test_tick_no_due_no_publish():
    pub = Pub()
    s = await _store_with(Reminder(user_id="u1", title="F", kind="time", fire_at=10 ** 12))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 100.0).tick()
    assert n == 0 and pub.sent == []


@pytest.mark.asyncio
async def test_tick_single_fired_payload_contract():
    pub = Pub()
    s = await _store_with(Reminder(user_id="u1", title="给客户回电话",
                                   kind="time", fire_at=100))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()
    assert n == 1 and len(pub.sent) == 1
    p = pub.sent[0]
    assert p["type"] == "reminder_fired" and p["agent_id"] == "reminder"
    assert "给客户回电话" in p["speech"]
    assert p["card"]["type"] == "reminder_card" and p["card"]["context"] == "fired"
    assert p["card"]["item"]["title"] == "给客户回电话"
    labels = [a["label"] for a in p["card"]["actions"]]
    assert labels == ["完成", "稍后10分钟"]
    assert p["card"]["actions"][1]["send_text"] == "10分钟后再提醒我给客户回电话"


@pytest.mark.asyncio
async def test_tick_merges_multiple_into_one_publish():
    pub = Pub()
    s = await _store_with(
        Reminder(user_id="u1", title="A", kind="time", fire_at=100),
        Reminder(user_id="u1", title="B", kind="time", fire_at=110))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()
    assert n == 2 and len(pub.sent) == 1          # 合并为一次播报，防连环轰炸
    p = pub.sent[0]
    assert "2 条" in p["speech"] and "A" in p["speech"] and "B" in p["speech"]
    assert p["card"]["type"] == "card_group"
    assert [c["item"]["title"] for c in p["card"]["items"]] == ["A", "B"]


@pytest.mark.asyncio
async def test_tick_survives_publish_failure():
    pub = Pub(fail=True)
    s = await _store_with(Reminder(user_id="u1", title="X", kind="time", fire_at=100))
    n = await ReminderScheduler(s, pub, now_fn=lambda: 200.0).tick()
    assert n == 1                                  # 已领取（fired 不回滚），失败仅日志
    assert (await s.claim_due(300)) == []          # 不会重复触发
```

- [ ] **Step 3.2 跑测失败** → **Step 3.3 实现 `agents/reminder/src/scheduler.py`**（完整文件）：

```python
"""到点触发调度：轮询 claim_due（原子领取）→ 合并成一次 NATS proactive 发布。

不节流（用户显式契约到点必响，与 road-safety 环境播报不同）；同批多条合并播报。
publish 失败仅日志——条目已 fired 不回滚（宁可漏一次播报，不重复轰炸；P1 补投兜底）。
"""
from __future__ import annotations
import asyncio
import logging
import os
import time

from .store import ReminderStore
from .timeparse import business_tz

logger = logging.getLogger("agent.reminder.scheduler")


class ReminderScheduler:
    def __init__(self, store: ReminderStore, publish, *,
                 poll_s: float | None = None, now_fn=time.time):
        self._store = store
        self._publish = publish          # async callable(payload: dict)
        self._poll_s = poll_s if poll_s is not None else float(os.getenv("REMINDER_POLL_S", "5"))
        self._now = now_fn
        self._tz = business_tz()

    async def tick(self) -> int:
        due = await self._store.claim_due(int(self._now()))
        if not due:
            return 0
        titles = [r.title for r in due]
        if len(due) == 1:
            speech = f"叮，到点了：{titles[0]}。"
        else:
            head = "、".join(titles[:3]) + ("等" if len(titles) > 3 else "")
            speech = f"有 {len(due)} 条提醒到点了：{head}。"
        cards = [{"type": "reminder_card", "context": "fired",
                  "item": r.to_card_item(tz=self._tz),
                  "actions": [
                      {"label": "完成", "send_text": f"完成提醒：{r.title}"},
                      {"label": "稍后10分钟", "send_text": f"10分钟后再提醒我{r.title}"},
                  ]} for r in due]
        payload = {"type": "reminder_fired", "speech": speech,
                   "card": cards[0] if len(cards) == 1 else
                   {"type": "card_group", "items": cards},
                   "agent_id": "reminder", "ts": int(self._now() * 1000),
                   "user_id": due[0].user_id}
        try:
            await self._publish(payload)
            logger.info("reminder fired x%d: %s", len(due), "、".join(titles)[:60])
        except Exception as e:
            logger.warning("reminder proactive publish failed（不回滚 fired）: %s", e)
        return len(due)

    async def run_forever(self):
        logger.info("reminder scheduler: poll every %.1fs", self._poll_s)
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.warning("reminder scheduler tick error: %s", e)
            await asyncio.sleep(self._poll_s)
```

- [ ] **Step 3.4 跑测通过**：`python -m pytest agents/reminder/tests/test_scheduler.py -q` → 4 PASS。
- [ ] **Step 3.5 提交**：

```bash
git add agents/reminder/src/scheduler.py agents/reminder/tests/test_scheduler.py
git commit -m "feat(reminder): 调度器 tick/run_forever（原子领取+同批合并播报+publish 失败不回滚）"
```

---

## Task 4：manifest / 入口 / 镜像 / 状态键登记

**Files:**
- Create: `agents/reminder/manifest.yaml`、`agents/reminder/main.py`、`agents/reminder/Dockerfile`、`agents/reminder/requirements.txt`
- Modify: `agents/_sdk/shared_state.py`

- [ ] **Step 4.1 写 `agents/reminder/manifest.yaml`**（完整文件，照设计 §6.2 定稿）：

```yaml
agent_id: reminder
version: 0.1.0
display_name: 智能提醒
category: core
trust_level: first_party
deployment: cloud
latency_budget_ms: 8000     # 常规确定性解析毫秒级；LLM 兜底解析一跳 @fast 留余量
fallback: chitchat

capabilities:
  - intent: reminder.create
    description: 创建定时提醒或待办。支持绝对时间（明天早上八点/周五下午三点）、
      相对时间（半小时后/20分钟后）；只说"记一下"不带时间则存为待办。
    slots: [title, time_text, kind]
    examples: ["明天早上八点提醒我带充电线", "半小时后提醒我给客户回电话",
               "周五下午三点叫我去接孩子", "记一下要买牛奶", "帮我记个待办周末洗车"]
  - intent: reminder.list
    description: 查询提醒/待办/日程安排（今天/明天/后天/未来三天/这周/全部/待办）
    slots: [scope, date_text]
    examples: ["我今天有什么安排", "我有哪些提醒", "看看我的待办", "明天有什么提醒",
               "这周有什么安排", "未来三天有什么提醒"]
  - intent: reminder.complete
    description: 完成某条提醒/待办（按序号或内容）
    slots: [index, title]
    examples: ["完成第二条", "买牛奶那条办完了", "标记第一个完成", "完成提醒：给客户回电话"]
  - intent: reminder.cancel
    description: 取消某条提醒，或清空全部（清空需二次确认）
    slots: [index, title, all]
    examples: ["取消第二条提醒", "不用提醒我回电话了", "把提醒都清空"]

# 确定性路由（R2.1）：弱 LLM 常把"提醒我X"落到 chitchat。guard 排除车辆功能语境的
# "提醒"（限速/车道/碰撞等设备对象词，见 orchestrator/edge/fast_intent.py:1090）与
# 说明书查询（"限速提醒是什么"归 manual）。pattern 为设计 §6.2 方向性草案，以
# test/eval_corpus/route_hints_cases.yaml 实测收敛（Task 10）。
route_hints:
  - pattern: '提醒我|叫我(?!.{0,4}(小舟|什么))|别忘了|帮我记(一?下|个)|记个待办|设个提醒'
    intent: reminder.create
    policy: replace
    priority: 56
    guard: '限速|车道|碰撞|疲劳|盲区|导航播报|电量提醒|保养提醒|是什么|怎么(开|关|用)|什么意思'
    slots: {title: "$text"}
  - pattern: '我(今天|明天|后天|这周|未来.{0,2}天)?(有什么|有哪些|的)(安排|提醒|待办|日程)|看看?(我的)?(提醒|待办|日程)'
    intent: reminder.list
    policy: replace
    priority: 56
    guard: '行程|旅行|路线'
    slots: {scope: "$text"}
  - pattern: '(取消|删掉|删除|不用).{0,4}(提醒|待办)|(提醒|待办).{0,4}(清空|全删)|把提醒都?清空'
    intent: reminder.cancel
    policy: replace
    priority: 56
    slots: {title: "$text"}
  - pattern: '(完成|办完|做完|搞定)了?.{0,4}(第[一二三四五六七八九十0-9]+[条个项]|那[条个]|提醒|待办)|完成提醒[:：]'
    intent: reminder.complete
    policy: replace
    priority: 56
    guard: '行程|导航|充电'
    slots: {title: "$text"}

requires_permissions:
  - profile.read        # 提醒属用户数据域 + shared_state 经 profile KV；不新增 scope
  - profile.write
edge_intents: []
context_scopes: []      # P0 不需要精确位置/电量；P1 位置触发时补 location
```

- [ ] **Step 4.2 写 `agents/reminder/main.py`**（完整文件）：

```python
"""reminder Agent 启动入口。"""
import asyncio

from agents._sdk import serve
from agents.reminder.src.agent import ReminderAgent

if __name__ == "__main__":
    asyncio.run(serve(ReminderAgent()))
```

- [ ] **Step 4.3 写 `agents/reminder/requirements.txt` 与 `Dockerfile`**：

`agents/reminder/requirements.txt`（叠加在 `_sdk/requirements.txt` 之上；nats-py 已在 _sdk 里，**不要重复**也不要动 _sdk）：

```
# reminder 专属：PG 持久层（仿 memory/pg_store.py；其余依赖来自 agents/_sdk/requirements.txt）
asyncpg>=0.29
```

`agents/reminder/Dockerfile`（仿 `agents/deep_research/Dockerfile`，多一层专属依赖）：

```dockerfile
# build context 为项目根。需先 `make proto`。
FROM python:3.11-slim
WORKDIR /app

COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY agents/reminder/requirements.txt /tmp/req-reminder.txt
RUN pip install --no-cache-dir -r /tmp/req-reminder.txt

COPY gen/python /app/gen/python
COPY agents /app/agents
COPY runtime /app/runtime
COPY observability /app/observability

ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=50074
CMD ["python", "agents/reminder/main.py"]
```

- [ ] **Step 4.4 登记状态键**（Modify `agents/_sdk/shared_state.py`）——在 `TRIP_ACTIVE = "trip_active"` 之后、`__all__` 之前加：

```python
# reminder 写当前提醒列表（list/create/complete/cancel 后刷新）→ 自身「第N条」序号解析读
REMINDERS_ACTIVE = "reminders_active"
# reminder create 缺时刻追问时写 {title} → 下一轮 create 合并标题；消费即清
REMINDER_PENDING = "reminder_pending"
```

并把 `__all__` 改为：

```python
__all__ = ["NEWS_ACTIVE", "RESEARCH_ACTIVE", "TRIP_ACTIVE",
           "REMINDERS_ACTIVE", "REMINDER_PENDING"]
```

同时在文件头表格补两行（owner=reminder，schema 同上注释）。

- [ ] **Step 4.5 校验与提交**：

```bash
python -m py_compile agents/reminder/main.py agents/_sdk/shared_state.py
python -c "from agents._sdk.manifest import load_manifest; m=load_manifest('agents/reminder/manifest.yaml'); print(m.agent_id, len(m.capabilities))"
git add agents/reminder agents/_sdk/shared_state.py
git commit -m "feat(reminder): manifest（4 intent + route_hints）/入口/镜像（asyncpg 叠加层）/状态键登记"
```

预期第二条输出：`reminder 4`（route_hints 若 loader 报未知字段，对照 `agents/nearby/manifest.yaml` 的字段名逐一核对——nearby 已在用，loader 必然支持）。

---

## Task 5：`agent.py` 四个 intent handler（契约测试 TDD）

**Files:**
- Test: `agents/reminder/tests/test_agent.py`
- Create: `agents/reminder/src/agent.py`

- [ ] **Step 5.1 写失败测试**（完整文件）：

```python
"""ReminderAgent 契约测试：不起 gRPC，直驱 handle（agents/_sdk/testing.py 夹具）。"""
import json
from unittest.mock import AsyncMock

import pytest

from datetime import datetime, timedelta, timezone

from agents._sdk.testing import make_context, run_handle, assert_manifest_consistent
from agents.reminder.src.agent import ReminderAgent
from agents.reminder.src.store import Reminder, ReminderStore
from agents.reminder.src.timeparse import FAIL, ParsedTime

_TZ = timezone(timedelta(hours=8))
_NOW = datetime(2026, 7, 11, 10, 0, tzinfo=_TZ).astimezone(timezone.utc)  # 周六 10:00


async def _agent() -> ReminderAgent:
    a = ReminderAgent()
    a.store = ReminderStore(dsn="")          # 每例独立内存 store
    await a.store.init()
    a._llm_time_fallback = AsyncMock(return_value=ParsedTime(FAIL))  # 默认 LLM 兜底失败
    a._now_utc = lambda: _NOW                # 固定时钟：用例不随跑测时刻漂移
    return a


def test_manifest():
    assert assert_manifest_consistent(ReminderAgent()) is True


@pytest.mark.asyncio
async def test_create_absolute_time():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    assert res.status == "ok"
    assert "明天" in res.speech and "08:00" in res.speech and "带充电线" in res.speech
    assert res.ui_card["type"] == "reminder_card" and res.ui_card["context"] == "created"
    times, _ = await a.store.list_split("u1")
    assert len(times) == 1 and times[0].title == "带充电线"


@pytest.mark.asyncio
async def test_create_relative_time():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="半小时后提醒我给客户回电话")
    assert res.status == "ok" and "给客户回电话" in res.speech


@pytest.mark.asyncio
async def test_create_without_time_asks_and_saves_pending():
    a = await _agent()
    ctx = make_context()
    res = await run_handle(a, "reminder.create", raw_text="提醒我开会", ctx=ctx)
    assert res.status == "need_slot" and "time_text" in res.missing_slots
    assert "什么时候" in res.speech
    # NEED_SLOT 时把标题存进 REMINDER_PENDING（经 profile KV）
    args = ctx._memory.upsert_profile.call_args
    assert args.args[1] == "reminder_pending" and "开会" in args.args[2]


@pytest.mark.asyncio
async def test_create_resumes_pending_title():
    a = await _agent()
    ctx = make_context(context_values={
        "profile.reminder_pending": json.dumps({"title": "买牛奶"}, ensure_ascii=False)})
    res = await run_handle(a, "reminder.create", raw_text="晚上八点", ctx=ctx)
    assert res.status == "ok" and "买牛奶" in res.speech


@pytest.mark.asyncio
async def test_create_todo_without_time():
    a = await _agent()
    res = await run_handle(a, "reminder.create", raw_text="记一下要买牛奶")
    assert res.status == "ok" and "买牛奶" in res.speech
    _, todos = await a.store.list_split("u1")
    assert len(todos) == 1 and todos[0].kind == "todo"


@pytest.mark.asyncio
async def test_list_today_writes_active_and_card():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="今晚八点提醒我取快递")
    ctx = make_context()
    res = await run_handle(a, "reminder.list", raw_text="我今天有什么安排", ctx=ctx)
    assert res.status == "ok"
    card = res.ui_card
    assert card["type"] == "reminder_list" and card["view"] == "day"
    assert card["items"][0]["title"] == "取快递"
    keys = [c.args[1] for c in ctx._memory.upsert_profile.call_args_list]
    assert "reminders_active" in keys


@pytest.mark.asyncio
async def test_list_week_is_multi_view():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.list", raw_text="这周有什么安排")
    assert res.ui_card["view"] == "multi"


@pytest.mark.asyncio
async def test_list_empty_honest():
    a = await _agent()
    res = await run_handle(a, "reminder.list", raw_text="我今天有什么安排")
    assert res.status == "ok" and "没有" in res.speech and res.ui_card is None


@pytest.mark.asyncio
async def test_complete_by_title():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.complete", raw_text="带充电线那条办完了")
    assert res.status == "ok" and "带充电线" in res.speech
    times, _ = await a.store.list_split("u1", statuses=("done",))
    assert len(times) == 1


@pytest.mark.asyncio
async def test_complete_by_ordinal_via_active_state():
    a = await _agent()
    r = await a.store.add(Reminder(user_id="u1", title="回电话", kind="time",
                                   fire_at=10 ** 12))
    ctx = make_context(context_values={"profile.reminders_active": json.dumps(
        {"items": [{"id": r.id, "title": "回电话"}]}, ensure_ascii=False)})
    res = await run_handle(a, "reminder.complete", raw_text="完成第一条", ctx=ctx)
    assert res.status == "ok" and "回电话" in res.speech


@pytest.mark.asyncio
async def test_cancel_single_and_not_found():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    res = await run_handle(a, "reminder.cancel", raw_text="不用提醒我带充电线了")
    assert res.status == "ok" and "取消" in res.speech
    res2 = await run_handle(a, "reminder.cancel", raw_text="取消买牛奶那条")
    assert res2.status == "failed" and "没找到" in res2.speech


@pytest.mark.asyncio
async def test_cancel_all_needs_confirm_then_executes():
    a = await _agent()
    await run_handle(a, "reminder.create", raw_text="明天早上八点提醒我带充电线")
    await run_handle(a, "reminder.create", raw_text="记一下要买牛奶")
    res = await run_handle(a, "reminder.cancel", raw_text="把提醒都清空")
    assert res.status == "need_confirm" and "2 条" in res.speech
    res2 = await run_handle(a, "reminder.cancel", raw_text="把提醒都清空",
                            meta={"confirmed": "true"})
    assert res2.status == "ok" and "清空" in res2.speech
    times, todos = await a.store.list_split("u1")
    assert times == [] and todos == []


@pytest.mark.asyncio
async def test_create_past_explicit_time_asks_again():
    a = await _agent()   # 固定时钟 10:00：今天凌晨一点必然已过
    res = await run_handle(a, "reminder.create", raw_text="今天凌晨一点提醒我看球")
    assert res.status == "need_slot" and "已经过了" in res.speech
```

- [ ] **Step 5.2 跑测确认失败**：`python -m pytest agents/reminder/tests/test_agent.py -q` → 收集期 `ModuleNotFoundError: agents.reminder.src.agent`。

- [ ] **Step 5.3 实现 `agents/reminder/src/agent.py`**（完整文件）：

```python
"""智能提醒 Agent：自然语言创建日程提醒/待办 + 列表/完成/取消 + 到点 proactive 触达。

设计：docs/design/2026-07-11-reminder-agent-design.md（已批准，含 D7）。
时间可测性：所有"现在"取 self._now_utc()（测试注入固定时钟）。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from agents._sdk import BaseAgent, AgentResult, NEED_CONFIRM, NEED_SLOT, FAILED
from agents._sdk.shared_state import REMINDERS_ACTIVE, REMINDER_PENDING

from .store import Reminder, ReminderStore, DONE, CANCELLED
from .timeparse import (OK as T_OK, FAIL as T_FAIL, ParsedTime, business_tz,
                        format_display, parse_time_text, strip_time_expressions)

logger = logging.getLogger("agent.reminder")

_MANIFEST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "manifest.yaml")
_PROACTIVE_SUBJECT = "agent.proactive"

_TODO_RE = re.compile(r"记一下|记个|待办|备忘")
_CMD_STRIP_RE = re.compile(
    r"^(麻烦|请|帮我|给我)?(再)?(提醒我|叫我|别忘了|记得|记一下|记个待办|记个|设个提醒|建个提醒|待办[:：]?)+")
_ORDINAL_RE = re.compile(r"第([一二三四五六七八九十0-9]+)\s*[条个项]?")
_ALL_RE = re.compile(r"全部|所有|都|清空|全删")
_CN_IDX = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


class ReminderAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.store = ReminderStore()
        self._nc = None
        self._tz = business_tz()
        self._sched_task = None

    # ── 生命周期：存储初始化 + NATS + 调度循环（road-safety 先例）──
    async def on_start(self) -> None:
        await self.store.init()
        nats_url = os.getenv("NATS_URL", "")
        if nats_url:
            try:
                import nats
                self._nc = await nats.connect(nats_url, max_reconnect_attempts=-1)
                logger.info("reminder: NATS 已连接，主动触达开启")
            except Exception as e:
                logger.warning("reminder: NATS 连接失败，主动触达禁用：%s", e)
        else:
            logger.info("reminder: NATS_URL 未设置，主动触达禁用")
        from .scheduler import ReminderScheduler
        self._sched_task = asyncio.create_task(
            ReminderScheduler(self.store, self._publish_proactive).run_forever())

    async def _publish_proactive(self, payload: dict) -> None:
        if not self._nc:
            logger.info("reminder fired（NATS 禁用未推送）: %s",
                        payload.get("speech", "")[:40])
            return
        await self._nc.publish(_PROACTIVE_SUBJECT,
                               json.dumps(payload, ensure_ascii=False).encode())

    # ── 请求-响应 ──
    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {"reminder.create": self._create, "reminder.list": self._list,
                    "reminder.complete": self._complete, "reminder.cancel": self._cancel}
        h = handlers.get(intent.name)
        if not h:
            return AgentResult(status=FAILED, speech="提醒助手暂不支持该请求。")
        return await h(intent, ctx, meta)

    # 测试注入点：所有"现在"经此取
    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _uid(ctx) -> str:
        return ctx.user_id or "u1"

    # ── create ──
    async def _create(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        title = (intent.slots.get("title") or "").strip()
        time_text = (intent.slots.get("time_text") or "").strip()
        if not title or title == raw:            # route_hints 灌整句 / planner 未抽槽
            title = self._extract_title(raw)
        if not title:
            title = await self._load_pending(ctx)  # 上一轮 NEED_SLOT 只差时间
        if not title:
            return AgentResult(status=NEED_SLOT, speech="要提醒你什么事？",
                               follow_up="比如：明天早上八点提醒我带充电线",
                               missing_slots=["title"])
        is_todo = intent.slots.get("kind") == "todo" or bool(
            _TODO_RE.search(raw) and not re.search(r"提醒|叫我", raw))
        if is_todo:
            r = await self.store.add(Reminder(
                user_id=self._uid(ctx), vehicle_id=ctx.vehicle_id or "",
                title=title, kind="todo"))
            await self._refresh_active(ctx)
            await self._clear_pending(ctx)
            return AgentResult(speech=f"记下了：{title}。办完了跟我说「完成」就行。",
                               ui_card=self._card_single(r, "created"))
        now = self._now_utc()
        pt = parse_time_text(time_text, now=now, tz=self._tz) if time_text \
            else ParsedTime(T_FAIL)
        if pt.status == T_FAIL:
            pt = parse_time_text(raw, now=now, tz=self._tz)
        if pt.status == T_FAIL:
            pt = await self._llm_time_fallback(time_text or raw)
        if pt.status != T_OK:
            await self._save_pending(ctx, title)
            return AgentResult(status=NEED_SLOT,
                               speech=f"好的，{title}。什么时候提醒你？",
                               follow_up="比如：明天早上八点 / 半小时后",
                               missing_slots=["time_text"])
        if pt.fire_at <= int(now.timestamp()):
            await self._save_pending(ctx, title)
            return AgentResult(status=NEED_SLOT,
                               speech=f"{pt.display}已经过了，换个时间？",
                               missing_slots=["time_text"])
        r = await self.store.add(Reminder(
            user_id=self._uid(ctx), vehicle_id=ctx.vehicle_id or "",
            title=title, kind="time", fire_at=pt.fire_at))
        await self._refresh_active(ctx)
        await self._clear_pending(ctx)
        return AgentResult(speech=f"好的，{pt.display}提醒你：{title}。",
                           ui_card=self._card_single(r, "created"))

    @staticmethod
    def _extract_title(raw: str) -> str:
        t = strip_time_expressions(raw or "")
        t = _CMD_STRIP_RE.sub("", t).strip()
        t = re.sub(r"^(我?要|去|该)", "", t)
        return t.strip(" ，。,、！!？?的哦啊呀吧")

    async def _llm_time_fallback(self, text: str) -> ParsedTime:
        """规则未命中（"下下周三饭点"）→ LLM @fast 抽 ISO；失败 FAIL（外层追问）。"""
        ln = self._now_utc().astimezone(self._tz)
        prompt = (f"现在是 {ln.strftime('%Y-%m-%d %H:%M')}"
                  f"（周{'一二三四五六日'[ln.weekday()]}，UTC+8）。\n"
                  f"用户说：「{text}」\n"
                  '解析其中的提醒时间，只输出 JSON：{"iso": "YYYY-MM-DDTHH:MM"}；'
                  '解析不出输出 {"iso": null}')
        try:
            out = await self.llm.complete(
                [{"role": "system", "content": "你是时间解析器，只输出 JSON。"},
                 {"role": "user", "content": prompt}],
                model=os.getenv("LLM_MODEL_FAST", ""), temperature=0.0,
                max_tokens=60, thinking=False)
            m = re.search(r"\{.*\}", out, re.S)
            iso = json.loads(m.group(0)).get("iso") if m else None
            if not iso:
                return ParsedTime(T_FAIL)
            dt = datetime.fromisoformat(iso).replace(tzinfo=self._tz)
            fire = int(dt.astimezone(timezone.utc).timestamp())
            return ParsedTime(T_OK, fire,
                              format_display(fire, now=self._now_utc(), tz=self._tz))
        except Exception as e:
            logger.debug("reminder: llm time fallback failed: %s", e)
            return ParsedTime(T_FAIL)

    # ── list（D7：scope 词表 + view 双形态）──
    async def _list(self, intent, ctx, meta) -> AgentResult:
        text = " ".join(filter(None, [intent.slots.get("scope", ""),
                                      intent.slots.get("date_text", ""),
                                      intent.raw_text or ""]))
        now_utc = self._now_utc()
        ln = now_utc.astimezone(self._tz)
        day0 = ln.replace(hour=0, minute=0, second=0, microsecond=0)

        def ep(dt):
            return int(dt.astimezone(timezone.utc).timestamp())

        view, label, frm, to, todo_only = "multi", "全部", 0, 0, False
        if "待办" in text and not re.search(r"提醒|日程|安排", text):
            todo_only, label = True, "待办"
        elif re.search(r"今天|今日", text):
            view, label = "day", f"今天 · {ln.month}月{ln.day}日"
            frm, to = ep(day0), ep(day0 + timedelta(days=1))
        elif "明天" in text:
            d = day0 + timedelta(days=1)
            view, label = "day", f"明天 · {d.month}月{d.day}日"
            frm, to = ep(d), ep(d + timedelta(days=1))
        elif "后天" in text:
            d = day0 + timedelta(days=2)
            view, label = "day", f"后天 · {d.month}月{d.day}日"
            frm, to = ep(d), ep(d + timedelta(days=1))
        elif re.search(r"未来.{0,2}天|最近几天|这几天", text):
            label, frm, to = "未来三天", ep(now_utc), ep(day0 + timedelta(days=3))
        elif re.search(r"这周|本周", text):
            label, frm, to = "这周", ep(now_utc), ep(day0 + timedelta(days=7 - ln.weekday()))
        # 词表外区间（如"下个月"）：P0 诚实回退"全部"（frm=0 含过期未办项），任意区间归 P1

        times, todos = await self.store.list_split(self._uid(ctx), from_ts=frm, to_ts=to)
        if todo_only:
            times = []
        total = len(times) + len(todos)
        if total == 0:
            return AgentResult(speech=f"{label}没有提醒或待办。想加一条直接说"
                                      f"「明天早上八点提醒我…」。")
        await self._refresh_active(ctx, times + todos)
        head = "、".join(
            f"{r.title}（{format_display(r.fire_at, now=now_utc, tz=self._tz)}）"
            if r.fire_at else r.title for r in (times + todos)[:3])
        speech = f"{label}共 {total} 条：{head}" + ("等。" if total > 3 else "。")
        card = {"type": "reminder_list", "view": view, "date_label": label,
                "items": [r.to_card_item(now=now_utc, tz=self._tz) for r in times],
                "todos": [r.to_card_item(now=now_utc, tz=self._tz) for r in todos]}
        return AgentResult(speech=speech, ui_card=card)

    # ── complete / cancel ──
    async def _complete(self, intent, ctx, meta) -> AgentResult:
        r = await self._resolve_target(ctx, intent.raw_text or "", intent.slots)
        if not r:
            return AgentResult(status=FAILED,
                               speech="没找到这条提醒，说「看看我的提醒」我给你列一下。")
        await self.store.set_status(self._uid(ctx), r.id, DONE)
        await self._refresh_active(ctx)
        return AgentResult(speech=f"「{r.title}」已完成。")

    async def _cancel(self, intent, ctx, meta) -> AgentResult:
        raw = intent.raw_text or ""
        wants_all = (intent.slots.get("all") or "").lower() in ("true", "1", "全部") \
            or bool(_ALL_RE.search(raw))
        if wants_all:
            times, todos = await self.store.list_split(self._uid(ctx))
            n = len(times) + len(todos)
            if n == 0:
                return AgentResult(speech="现在没有提醒或待办。")
            if (meta or {}).get("confirmed") == "true":   # engine 确认续接（R2 契约）
                await self.store.cancel_all(self._uid(ctx))
                await self._refresh_active(ctx, [])
                return AgentResult(speech=f"好的，已清空全部 {n} 条提醒和待办。")
            return AgentResult(status=NEED_CONFIRM,
                               speech=f"确定要清空全部 {n} 条提醒和待办吗？清掉就找不回来了。")
        r = await self._resolve_target(ctx, raw, intent.slots)
        if not r:
            return AgentResult(status=FAILED,
                               speech="没找到这条提醒，说「看看我的提醒」我给你列一下。")
        await self.store.set_status(self._uid(ctx), r.id, CANCELLED)
        await self._refresh_active(ctx)
        return AgentResult(speech=f"好的，取消了「{r.title}」。")

    async def _resolve_target(self, ctx, raw: str, slots: dict) -> Reminder | None:
        """序号经 REMINDERS_ACTIVE（须本会话列过/建过）；标题直接查 store 子串匹配。"""
        uid = self._uid(ctx)
        idx = None
        idx_slot = (slots.get("index") or "").strip()
        if idx_slot.isdigit():
            idx = int(idx_slot)
        if idx is None:
            m = _ORDINAL_RE.search(idx_slot + " " + raw)
            if m:
                v = m.group(1)
                idx = int(v) if v.isdigit() else _CN_IDX.get(v)
        if idx:
            data = await ctx.load_shared_state(REMINDERS_ACTIVE)
            try:
                d = json.loads(data) if isinstance(data, str) else (data or {})
                items = d.get("items", [])
            except Exception:
                items = []
            if 0 < idx <= len(items):
                return await self.store.get(uid, items[idx - 1]["id"])
            return None
        q = (slots.get("title") or "").strip()
        if not q or q == raw:
            q = self._extract_title(re.sub(
                r"完成提醒[:：]|完成|办完|做完|搞定|取消|删掉|删除|不用|那条|这条|了", "", raw))
        hits = await self.store.find_by_title(uid, q) if q else []
        return hits[0] if hits else None

    # ── shared_state（conventions §9）──
    async def _refresh_active(self, ctx, items: list | None = None) -> None:
        if items is None:
            times, todos = await self.store.list_split(self._uid(ctx))
            items = times + todos
        await ctx.save_shared_state(REMINDERS_ACTIVE, {
            "items": [{"id": r.id, "title": r.title} for r in items[:10]]})

    async def _save_pending(self, ctx, title: str) -> None:
        await ctx.save_shared_state(REMINDER_PENDING, {"title": title})

    async def _clear_pending(self, ctx) -> None:
        await ctx.save_shared_state(REMINDER_PENDING, {})

    async def _load_pending(self, ctx) -> str:
        data = await ctx.load_shared_state(REMINDER_PENDING)
        try:
            d = json.loads(data) if isinstance(data, str) else (data or {})
            return (d.get("title") or "").strip()
        except Exception:
            return ""

    def _card_single(self, r: Reminder, context: str) -> dict:
        return {"type": "reminder_card", "context": context,
                "item": r.to_card_item(now=self._now_utc(), tz=self._tz)}
```

- [ ] **Step 5.4 跑测通过**：

```bash
python -m py_compile agents/reminder/src/agent.py
python -m pytest agents/reminder/tests -q
```

预期：**timeparse + store + scheduler + agent 全部 PASS**（约 60+ 用例）。

- [ ] **Step 5.5 提交**：

```bash
git add agents/reminder/src/agent.py agents/reminder/tests/test_agent.py
git commit -m "feat(reminder): 四 intent handler（create 追问续接/todo/list 双形态/序号+标题定位/清空确认）"
```

---

## Task 6：HMI 契约与纯逻辑（types.ts + reminderStage.mjs，node TDD）

**Files:**
- Modify: `hmi/src/types.ts`
- Test: `hmi/src/reminderStage.test.mjs`
- Create: `hmi/src/reminderStage.mjs`

- [ ] **Step 6.1 扩 `hmi/src/types.ts`**（三处）：

①在 `PlaceDetailCard` 类型定义之后插入（设计 §9.1 定稿）：

```ts
// 智能提醒（reminder Agent）：单条项契约——time_display 后端本地化，HMI 不做时区运算
export type ReminderItem = {
  id: string
  title: string
  kind: 'time' | 'todo'
  status: 'pending' | 'fired' | 'done' | 'cancelled'
  time_display?: string   // "今天 14:30" / "明天 08:00"
  fire_at_ms?: number     // agenda 时间轴定位用；todo 无
}

// 提醒列表卡（reminder.list）：view 驱动右舞台形态（D7；后端按查询范围权威给出）
export type ReminderListCard = {
  type: 'reminder_list'
  view?: 'day' | 'multi'
  date_label?: string     // day："今天 · 7月11日"；multi："这周"
  items: ReminderItem[]
  todos?: ReminderItem[]  // 无时间待办单列
}

// 提醒单条卡：created=创建回读确认 / fired=到点触达（带 完成/稍后 按钮，send_text 模式）
export type ReminderCard = {
  type: 'reminder_card'
  context: 'created' | 'fired'
  item: ReminderItem
  actions?: Array<{ label: string; send_text: string }>
}
```

②`UiCard` 联合类型在 `| PlaceDetailCard` 之后加两行：

```ts
  | ReminderListCard
  | ReminderCard
```

③`AGENT_CATALOG` 在 nearby 行后加：

```ts
  { id: 'reminder', label: '智能提醒', desc: '说一句话创建日程提醒待办，到点主动叫你', icon: '⏰' },
```

- [ ] **Step 6.2 写失败测试 `hmi/src/reminderStage.test.mjs`**（完整文件）：

```js
import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveView, dayLabel, groupByDay, timelineWindow, yForTime } from './reminderStage.mjs'

const now = new Date(2026, 6, 11, 10, 0).getTime()   // 2026-07-11(周六) 10:00 本地
const at = (dayOff, h, m = 0) => new Date(2026, 6, 11 + dayOff, h, m).getTime()
const item = (t, ms) => ({ id: t, title: t, kind: 'time', status: 'pending', fire_at_ms: ms })

test('resolveView 后端权威，缺省保守走 multi', () => {
  assert.equal(resolveView({ view: 'day' }), 'day')
  assert.equal(resolveView({ view: 'multi' }), 'multi')
  assert.equal(resolveView({}), 'multi')
  assert.equal(resolveView(null), 'multi')
})

test('dayLabel 今天/明天/后天/具体日期', () => {
  assert.equal(dayLabel(at(0, 15), now), '今天')
  assert.equal(dayLabel(at(1, 8), now), '明天')
  assert.equal(dayLabel(at(2, 8), now), '后天')
  assert.equal(dayLabel(at(3, 9), now), '7月14日(周二)')
})

test('groupByDay 按天分组 + 封顶 + 还有N条', () => {
  const items = [item('A', at(0, 15)), item('B', at(0, 20)), item('C', at(1, 8)),
                 item('D', at(2, 9)), item('E', at(3, 9)), item('F', at(4, 9)),
                 item('G', at(5, 9))]
  const { groups, more } = groupByDay(items, now, 6)
  assert.equal(more, 1)                                    // 7 条封顶 6
  assert.deepEqual(groups.map((g) => g.label),
    ['今天', '明天', '后天', '7月14日(周二)', '7月15日(周三)'])
  assert.deepEqual(groups[0].items.map((i) => i.title), ['A', 'B'])
})

test('groupByDay 跳过无时间项（待办另走 TodoStrip）', () => {
  const { groups, more } = groupByDay([{ id: 't', title: 't', kind: 'todo', status: 'pending' }], now)
  assert.deepEqual(groups, [])
  assert.equal(more, 0)
})

test('timelineWindow 动态取窗与空缺省', () => {
  assert.deepEqual(timelineWindow([], now), { startH: 8, endH: 22 })
  const w = timelineWindow([item('A', at(0, 15)), item('B', at(0, 20))], now)
  assert.equal(w.startH, 9)    // min(15,20,当前10)-1
  assert.equal(w.endH, 22)     // max(20)+2
})

test('yForTime 线性映射并夹紧边界', () => {
  assert.equal(yForTime(at(0, 8), 8, 22, 140), 0)
  assert.equal(yForTime(at(0, 22), 8, 22, 140), 140)
  assert.equal(yForTime(at(0, 15), 8, 22, 140), 70)
  assert.equal(yForTime(at(0, 6), 8, 22, 140), 0)          // 窗外夹紧
})
```

- [ ] **Step 6.3 跑测失败**：`cd hmi && node --test src/reminderStage.test.mjs` → `Cannot find module`。

- [ ] **Step 6.4 实现 `hmi/src/reminderStage.mjs`**（完整文件）：

```js
// agenda 舞台纯逻辑（node 可测；ContextualStage.tsx 只渲染）。
// 时间基准：浏览器本地时区（座舱本机）；time_display 展示文本仍以后端为权威。

export function resolveView(card) {
  if (card && (card.view === 'day' || card.view === 'multi')) return card.view
  return 'multi' // 后端权威给 view；缺失保守走 multi（分组列表对任意数据都成立）
}

function startOfDay(ms) {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

export function dayLabel(ms, nowMs) {
  const diff = Math.round((startOfDay(ms) - startOfDay(nowMs)) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff === 2) return '后天'
  const dt = new Date(ms)
  return `${dt.getMonth() + 1}月${dt.getDate()}日(周${'日一二三四五六'[dt.getDay()]})`
}

// 按天分组（items 已按 fire_at 升序，后端排好）；全局封顶 cap 条保一瞥性（D7）
export function groupByDay(items, nowMs, cap = 6) {
  const dated = (items || []).filter((it) => it.fire_at_ms)
  const shown = dated.slice(0, cap)
  const groups = []
  for (const it of shown) {
    const label = dayLabel(it.fire_at_ms, nowMs)
    const last = groups[groups.length - 1]
    if (last && last.label === label) last.items.push(it)
    else groups.push({ label, items: [it] })
  }
  return { groups, more: Math.max(0, dated.length - shown.length) }
}

// 单日时间轴取窗：最早条目前 1h ～ 最晚条目后 2h，含当前时刻；空缺省 08–22
export function timelineWindow(items, nowMs) {
  const hours = (items || []).filter((it) => it.fire_at_ms)
    .map((it) => new Date(it.fire_at_ms).getHours())
  if (!hours.length) return { startH: 8, endH: 22 }
  const startH = Math.max(0, Math.min(...hours, new Date(nowMs).getHours()) - 1)
  const endH = Math.min(24, Math.max(...hours) + 2)
  return { startH, endH: Math.max(endH, startH + 4) }
}

export function yForTime(ms, startH, endH, height) {
  const d = new Date(ms)
  const h = d.getHours() + d.getMinutes() / 60
  const t = Math.min(1, Math.max(0, (h - startH) / (endH - startH)))
  return Math.round(t * height)
}
```

- [ ] **Step 6.5 跑测通过 + tsc**：

```bash
cd hmi && node --test src/reminderStage.test.mjs && npx tsc --noEmit && cd ..
```

预期：6 test PASS；tsc 零新错（`.mjs` 无声明文件为预存噪声口径，不新增红线）。

- [ ] **Step 6.6 提交**：

```bash
git add hmi/src/types.ts hmi/src/reminderStage.mjs hmi/src/reminderStage.test.mjs
git commit -m "feat(hmi): 提醒卡片契约（reminder_list/reminder_card + catalog）与 agenda 舞台纯逻辑"
```

---

## Task 7：Cards.tsx 两卡渲染

**Files:**
- Modify: `hmi/src/components/Cards.tsx`（`CardRenderer` switch 在 `Cards.tsx:100-128`）

- [ ] **Step 7.1 加渲染组件**（文件内新增两个组件，风格对齐既有卡：`au-glass`/`au-num`/行车可读字号）：

```tsx
function ReminderListCardView({ card }: { card: ReminderListCard }) {
  const color = (s: string) =>
    s === 'fired' ? '#F59E0B' : s === 'done' ? 'var(--au-text-3)' : 'var(--au-primary)'
  const total = card.items.length + (card.todos?.length || 0)
  return (
    <div className="au-glass" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{card.date_label || '我的提醒'}</span>
        <span style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>{total} 条</span>
      </div>
      {card.items.map((it) => (
        <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="au-num" style={{ fontSize: 12.5, minWidth: 86, color: color(it.status) }}>{it.time_display}</span>
          <span style={{ fontSize: 13.5, flex: 1, textDecoration: it.status === 'done' ? 'line-through' : 'none', opacity: it.status === 'done' ? 0.55 : 1 }}>{it.title}</span>
          {it.status === 'fired' && <span style={{ fontSize: 10.5, color: '#F59E0B' }}>到点</span>}
        </div>
      ))}
      {(card.todos?.length || 0) > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, paddingTop: 6, borderTop: '1px solid var(--au-line-2)' }}>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)', width: '100%' }}>待办 · {card.todos!.length}</span>
          {card.todos!.map((t) => (
            <span key={t.id} className="au-glass" style={{ padding: '4px 10px', fontSize: 12, textDecoration: t.status === 'done' ? 'line-through' : 'none' }}>{t.title}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function ReminderCardView({ card, onAction }: { card: ReminderCard; onAction?: (text: string) => void }) {
  const fired = card.context === 'fired'
  const it = card.item
  const accent = fired ? '#F59E0B' : 'var(--au-primary)'
  return (
    <div className="au-glass" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10,
      ...(fired ? { animation: 'au-proactive-pulse-amber 3s ease-in-out infinite', border: '1px solid rgba(245,158,11,0.35)' } : {}) }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: accent, boxShadow: `0 0 8px ${accent}` }} />
        <span style={{ fontSize: 12, color: 'var(--au-text-3)' }}>{fired ? '提醒到点' : '已创建提醒'}</span>
        {it.time_display && <span className="au-num" style={{ marginLeft: 'auto', fontSize: 12.5, color: fired ? '#F59E0B' : 'var(--au-text-2)' }}>{it.time_display}</span>}
      </div>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{it.title}</div>
      {fired && (card.actions?.length || 0) > 0 && (
        <div style={{ display: 'flex', gap: 8 }}>
          {card.actions!.map((a) => (
            <button key={a.label} onClick={() => onAction?.(a.send_text)} className="au-glass"
              style={{ padding: '7px 14px', fontSize: 12.5, cursor: 'pointer', border: '1px solid var(--au-line-2)', background: 'transparent', color: 'var(--au-text)' }}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 7.2 接入 switch 与类型导入**：type import 行补 `ReminderListCard, ReminderCard`；在 `case 'intent_choice'`（`Cards.tsx:125`）之前插入：

```tsx
    case 'reminder_list': return <ReminderListCardView card={card} />
    case 'reminder_card': return <ReminderCardView card={card} onAction={onAction} />
```

- [ ] **Step 7.3 构建验证 + 提交**：

```bash
cd hmi && npm test && npm run build && cd ..
git add hmi/src/components/Cards.tsx
git commit -m "feat(hmi): reminder_list/reminder_card 渲染（fired 琥珀脉冲 + 完成/稍后 send_text 按钮）"
```

> 按钮回发用既有 `onAction`（`intent_choice` 先例：`send_text` → 作为新指令走正常链路）。

---

## Task 8：右舞台 AgendaStage 双形态（D7）

**Files:**
- Modify: `hmi/src/components/ContextualStage.tsx`

- [ ] **Step 8.1 场景推导接入**（三处小改）：

①type import 行补 `ReminderListCard, ReminderCard, ReminderItem`；文件头加：

```tsx
import { resolveView, groupByDay, timelineWindow, yForTime } from '../reminderStage.mjs'
```

②`Scene` 联合类型加一行：

```tsx
  | { kind: 'agenda'; card: UiCard }
```

③`deriveScene`（`ContextualStage.tsx:23-31`）在 weather 判断之前插入：

```tsx
      if (c.type === 'reminder_list' || c.type === 'reminder_card') return { kind: 'agenda', card: c }
```

`ContextualStage` 渲染分支加：

```tsx
      ) : scene.kind === 'agenda' ? (
        <AgendaStage card={scene.card} />
```

- [ ] **Step 8.2 实现 AgendaStage（完整新增代码，追加到文件尾）**：

```tsx
// ── 日程场景（D7 双形态）：单日=时间轴+当前时刻线；多日/全部=按天分组列表（封顶一瞥）──
function AgendaStage({ card }: { card: UiCard }) {
  const now = Date.now()
  const list = card.type === 'reminder_list' ? (card as ReminderListCard) : null
  const single = card.type === 'reminder_card' ? (card as ReminderCard) : null
  const items = list ? list.items : single ? [single.item] : []
  const todos = list?.todos || []
  const view = single ? 'day' : resolveView(list)
  const firedId = single?.context === 'fired' ? single.item.id : null
  const title = list?.date_label || (single?.context === 'fired' ? '提醒到点' : '今日日程')

  return (
    <div style={{ position: 'absolute', inset: 0, borderRadius: 'var(--au-r-3xl)', overflow: 'hidden', background: 'linear-gradient(158deg,#06080F 0%,#0B1020 60%,#080D18 100%)' }}>
      {/* 到点=AI 时刻：屏幕边缘极光（复用天气场景语言） */}
      {firedId && <div style={{ position: 'absolute', inset: 0, borderRadius: 'var(--au-r-3xl)', border: '1.5px solid transparent', background: 'linear-gradient(rgba(0,0,0,0),rgba(0,0,0,0)) padding-box, var(--au-aurora) border-box', animation: 'au-edge-pulse 3.5s ease-in-out infinite', pointerEvents: 'none', zIndex: 6 }} />}
      <div style={{ position: 'absolute', top: 18, left: 18, padding: '5px 13px', borderRadius: 20, background: 'rgba(70,214,224,0.10)', border: '1px solid rgba(70,214,224,0.22)', display: 'inline-flex', alignItems: 'center', gap: 7, zIndex: 5 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--au-primary)', boxShadow: '0 0 8px var(--au-primary)' }} />
        <span style={{ fontSize: 12.5, color: 'var(--au-primary)', fontWeight: 500 }}>{title}</span>
      </div>
      {view === 'day'
        ? <DayTimelineView items={items} now={now} firedId={firedId} hasTodos={todos.length > 0} />
        : <MultiAgendaView items={items} now={now} />}
      <TodoStrip todos={todos} />
      <div style={{ position: 'absolute', bottom: 16, right: 20, fontSize: 11, color: 'var(--au-text-3)', fontFamily: 'var(--au-font-mono)' }}>{items.length + todos.length} 条 · 日程</div>
    </div>
  )
}

function DayTimelineView({ items, now, firedId, hasTodos }: { items: ReminderItem[]; now: number; firedId: string | null; hasTodos: boolean }) {
  const H = 320
  const { startH, endH } = timelineWindow(items, now)
  const nowH = new Date(now).getHours() + new Date(now).getMinutes() / 60
  const nowY = nowH >= startH && nowH <= endH ? ((nowH - startH) / (endH - startH)) * H : null
  const ticks: number[] = []
  for (let h = startH; h <= endH; h += 2) ticks.push(h)
  const color = (s: string) => s === 'fired' ? '#F59E0B' : s === 'done' ? 'rgba(255,255,255,0.35)' : 'var(--au-primary)'
  return (
    <div style={{ position: 'absolute', top: 70, left: 46, right: 30, bottom: hasTodos ? 100 : 46 }}>
      <div style={{ position: 'relative', height: H, maxHeight: '100%' }}>
        <div style={{ position: 'absolute', left: 54, top: 0, bottom: 0, width: 1, background: 'var(--au-line-2)' }} />
        {ticks.map((h) => (
          <span key={h} className="au-num" style={{ position: 'absolute', left: 0, top: ((h - startH) / (endH - startH)) * H - 7, fontSize: 10.5, color: 'var(--au-text-3)' }}>{String(h).padStart(2, '0')}:00</span>
        ))}
        {nowY != null && (
          <div style={{ position: 'absolute', left: 40, right: 0, top: nowY, height: 2, background: 'var(--au-aurora)', borderRadius: 1, boxShadow: '0 0 10px rgba(91,233,255,0.4)' }} />
        )}
        {items.map((it) => {
          if (!it.fire_at_ms) return null
          const y = yForTime(it.fire_at_ms, startH, endH, H)
          const c = color(it.status)
          const pulse = it.id === firedId || it.status === 'fired'
          return (
            <div key={it.id} style={{ position: 'absolute', left: 48, top: y - 12, display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ width: 13, height: 13, borderRadius: '50%', background: 'rgba(70,214,224,0.12)', border: `1.5px solid ${c}`, ...(pulse ? { animation: 'au-proactive-pulse-amber 2.5s ease-in-out infinite' } : {}) }} />
              <span className="au-glass" style={{ padding: '6px 12px', display: 'inline-flex', gap: 10, alignItems: 'center' }}>
                <span className="au-num" style={{ fontSize: 12, color: c }}>{(it.time_display || '').split(' ').pop()}</span>
                <span style={{ fontSize: 13, textDecoration: it.status === 'done' ? 'line-through' : 'none', opacity: it.status === 'done' ? 0.55 : 1 }}>{it.title}</span>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function MultiAgendaView({ items, now }: { items: ReminderItem[]; now: number }) {
  const { groups, more } = groupByDay(items, now, 6)
  return (
    <div style={{ position: 'absolute', top: 70, left: 40, right: 30, bottom: 100, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'hidden' }}>
      {groups.map((g: { label: string; items: ReminderItem[] }) => (
        <div key={g.label}>
          <div style={{ fontSize: 12, color: 'var(--au-text-3)', marginBottom: 6 }}>{g.label}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {g.items.map((it) => (
              <div key={it.id} className="au-glass" style={{ padding: '8px 14px', display: 'flex', gap: 12, alignItems: 'center' }}>
                <span className="au-num" style={{ fontSize: 12.5, minWidth: 44, color: it.status === 'fired' ? '#F59E0B' : 'var(--au-primary)' }}>{(it.time_display || '').split(' ').pop()}</span>
                <span style={{ fontSize: 13.5, textDecoration: it.status === 'done' ? 'line-through' : 'none', opacity: it.status === 'done' ? 0.55 : 1 }}>{it.title}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {more > 0 && <div style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>⋯ 还有 {more} 条</div>}
    </div>
  )
}

function TodoStrip({ todos }: { todos: ReminderItem[] }) {
  if (!todos.length) return null
  return (
    <div style={{ position: 'absolute', left: 24, right: 24, bottom: 44, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>待办 · {todos.length}</span>
      {todos.slice(0, 4).map((t) => (
        <span key={t.id} className="au-glass" style={{ padding: '6px 12px', fontSize: 12.5, textDecoration: t.status === 'done' ? 'line-through' : 'none' }}>{t.title}</span>
      ))}
      {todos.length > 4 && <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>+{todos.length - 4}</span>}
    </div>
  )
}
```

- [ ] **Step 8.3 构建验证 + 提交**：

```bash
cd hmi && npm test && npm run build && cd ..
git add hmi/src/components/ContextualStage.tsx
git commit -m "feat(hmi): 右舞台 agenda 场景双形态（单日时间轴+当前时刻线 / 多日按天分组，fired 极光脉冲）"
```

> 视觉自查口径：徽标/底纹/极光边逐一复用 MapStage/WeatherStage 既有写法，不新增设计语言（设计 §9.2）。

---

## Task 9：compose / env / 文档登记（先文档后代码原则，与代码同批提交）

**Files:**
- Modify: `deploy/docker-compose.yaml`、`.env.example`、`docs/conventions.md`、`docs/design/README.md`

- [ ] **Step 9.1 compose 注册**（加在 `deep-research-agent` 块之后，`deploy/docker-compose.yaml:369` 附近）：

```yaml
  reminder-agent:
    <<: *restart
    build: { context: .., dockerfile: agents/reminder/Dockerfile }
    volumes: *certs-vol
    environment:
      <<: *python-env
      AGENT_PORT: "50074"
      # PG 持久 + 到点调度（无 PG 内存降级重启丢失；无 NATS 仅日志不推送）
      POSTGRES_DSN: postgresql://cockpit:cockpit@postgres:5432/cockpit
      REMINDER_POLL_S: ${REMINDER_POLL_S:-5}
      REMINDER_TZ: ${REMINDER_TZ:-Asia/Shanghai}
    depends_on: [registry, llm-gateway, memory, postgres, nats]
```

- [ ] **Step 9.2 `.env.example` 追加**（可选段，带注释）：

```bash
# ── 智能提醒（reminder Agent）──
# 调度轮询秒（到点触发精度）；业务时区（中文时间表达解析与展示）
REMINDER_POLL_S=5
REMINDER_TZ=Asia/Shanghai
```

- [ ] **Step 9.3 `docs/conventions.md` 四处登记**：
  - §1 Agent 清单总表加行：`| reminder | reminder | core | first_party | cloud | 50074 | reminder.create, reminder.list, reminder.complete, reminder.cancel |`；并把表下"规划中"注释的起始端口改为 **50075 起**。
  - §2 Intent 全集加 4 行（槽位/备注照 manifest：create=title,time_text,kind「缺时刻 NEED_SLOT 追问；记一下→待办」、list=scope,date_text「D7 词表+view 双形态」、complete=index,title、cancel=index,title,all「清空 NEED_CONFIRM」）。
  - §5 端口表 Agent 段改 `50061–50069, 50072–50074`，尾注"新 Agent 从 50075 起"。
  - §6 环境变量表加 `REMINDER_POLL_S` / `REMINDER_TZ` 两行；§9 跨 Agent 状态键表加 `REMINDERS_ACTIVE`、`REMINDER_PENDING` 两行（owner/reader=reminder，schema 同 shared_state.py 注释）。

- [ ] **Step 9.4 提交**：

```bash
git add deploy/docker-compose.yaml .env.example docs/conventions.md
git commit -m "chore(reminder): compose 注册 50074 + env 样例 + conventions 四表登记"
```

---

## Task 10：路由评测语料（对真实 manifest 实测收敛 pattern）

**Files:**
- Modify: `test/eval_corpus/route_hints_cases.yaml`

- [ ] **Step 10.1 追加用例**（文件尾，schema 见该文件头注释）：

```yaml
# ── reminder（2026-07-11 设计 §6.3）：正例路由 + 车辆功能"提醒"反例不被劫持 ──
- text: 明天早上八点提醒我带充电线
  initial_intents: []
  expect_final_intents: [reminder.create]
  source: "docs/design/2026-07-11-reminder-agent-design.md §6"
  tags: [reminder, replace]

- text: 半小时后提醒我给客户回电话
  initial_intents: []
  expect_final_intents: [reminder.create]
  source: "docs/design/2026-07-11-reminder-agent-design.md §6"
  tags: [reminder, replace]

- text: 我今天有什么安排
  initial_intents: []
  expect_final_intents: [reminder.list]
  source: "docs/design/2026-07-11-reminder-agent-design.md §6"
  tags: [reminder, list]

- text: 取消第二条提醒
  initial_intents: []
  expect_final_intents: [reminder.cancel]
  source: "docs/design/2026-07-11-reminder-agent-design.md §6"
  tags: [reminder, cancel]

- text: 打开限速提醒
  initial_intents: []
  expect_final_intents: []       # guard 拦下：车辆 ADAS 设置归端侧，云侧 hint 不接
  source: "orchestrator/edge/fast_intent.py:1090 词面冲突消解"
  tags: [reminder, guard, adas]

- text: 把车道偏离提醒关掉
  initial_intents: []
  expect_final_intents: []
  source: "docs/design/2026-07-11-reminder-agent-design.md §6.3"
  tags: [reminder, guard, adas]

- text: 限速提醒是什么意思
  initial_intents: []
  expect_final_intents: []       # 说明书查询：guard「是什么/什么意思」拦下
  source: "docs/design/2026-07-11-reminder-agent-design.md §6.3"
  tags: [reminder, guard, manual]
```

- [ ] **Step 10.2 实测收敛**（R3.4 口径：对真实 manifest 跑，不对着简化 fixture 想当然）：

```bash
python test/eval_route_hints.py 2>&1 | tail -15
```

预期：新增 7 例全 PASS 且既有用例零回归。**任何一例不符** → 回 `agents/reminder/manifest.yaml` 调 pattern/guard（如发现与 trip/nearby/sports 的跨 hint 交互，照该 yaml 头注释的先例把实测行为钉进用例注释），不改评测脚本。

- [ ] **Step 10.3 提交**：

```bash
git add test/eval_corpus/route_hints_cases.yaml agents/reminder/manifest.yaml
git commit -m "test(reminder): 路由评测正反例 7 条（ADAS 提醒/说明书查询 guard 实测收敛）"
```

---

## Task 11：真栈 e2e（创建→到点 NATS→列表/完成→清空确认，自清理可重入）

**Files:**
- Create: `test/e2e_reminder.py`

- [ ] **Step 11.1 写 `test/e2e_reminder.py`**（完整文件）：

```python
"""真栈闭环：WS 创建（相对秒级）→ NATS agent.proactive 收 reminder_fired（带卡）
→ 列表（fired 未完成仍可见）→ 完成 → 清空确认续接（自清理可重入）。

前置：make up 起全栈。依赖：pip install websockets nats-py
用法：python test/e2e_reminder.py
"""
import asyncio
import json
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
NATS_URL = "nats://localhost:4222"
SESSION = f"e2e-reminder-{int(time.time())}"
TIMEOUT = 60
_results: list[bool] = []


def record(name: str, ok: bool, detail: str = ""):
    _results.append(ok)
    print(f"{'✅' if ok else '❌'} {name}  {detail}")


async def ask(text: str, desc: str) -> dict:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": SESSION}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                print(f"  [{desc}] {text} → {msg.get('speech', msg.get('message', ''))[:60]}")
                return msg


async def main() -> int:
    # 1) 创建（20秒后）→ 回读确认
    r = await ask("20秒后提醒我E2E演练提醒", "创建")
    record("1.创建回读", r.get("type") == "final" and "E2E演练提醒" in r.get("speech", ""))

    # 2/3) 订 NATS 等 reminder_fired（20s 相对时间 + 5s 轮询 → 40s 内必到）
    got: list[dict] = []
    try:
        import nats
        nc = await nats.connect(NATS_URL)

        async def on_msg(m):
            try:
                p = json.loads(m.data.decode())
                if p.get("agent_id") == "reminder":
                    got.append(p)
            except Exception:
                pass

        sub = await nc.subscribe("agent.proactive", cb=on_msg)
        for _ in range(80):
            if got:
                break
            await asyncio.sleep(0.5)
        await sub.unsubscribe()
        await nc.close()
    except Exception as e:
        print(f"  NATS 订阅失败：{e}")
    ok_fire = bool(got) and got[0].get("type") == "reminder_fired" \
        and "E2E演练提醒" in got[0].get("speech", "")
    card_type = (got[0].get("card") or {}).get("type", "") if got else ""
    record("2.到点触达(NATS)", ok_fire, got[0].get("speech", "")[:40] if got else "未收到")
    record("3.触达带卡", card_type in ("reminder_card", "card_group"), card_type)

    # 4) 列表：fired 未完成仍可见（诚实呈现，设计 §4）
    r = await ask("我今天有什么安排", "列表")
    record("4.列表含该条", "E2E演练提醒" in r.get("speech", ""))

    # 5) 完成（fired → done）
    r = await ask("完成提醒：E2E演练提醒", "完成")
    record("5.完成", "已完成" in r.get("speech", ""))

    # 6) 清空：NEED_CONFIRM → 确认续接（engine meta.confirmed 契约）；也是自清理
    r = await ask("把提醒都清空", "清空请求")
    if r.get("need_confirm"):
        r2 = await ask("确定", "确认")
        record("6.清空确认闭环", "清空" in r2.get("speech", ""))
    else:
        record("6.清空确认闭环", "没有" in r.get("speech", ""), "已无活动项，直答")

    print(f"\n{'ALL PASS' if all(_results) else 'FAILED'} ({sum(_results)}/{len(_results)})")
    return 0 if all(_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 11.2 起真栈并跑通**：

```bash
docker compose -f compose.yaml up -d --build reminder-agent hmi
docker compose -f compose.yaml logs reminder-agent --tail 20   # 应见 "PG 就绪" 与 "NATS 已连接"
python test/e2e_reminder.py
```

预期：`ALL PASS (6/6)`。常见坑：①镜像缺 asyncpg → 回查 Dockerfile 双层 pip（Task 4）；②收不到 NATS → 查 compose 的 NATS_URL 注入与 `agent.proactive` 主题名；③"20秒后"没触发 → 查容器时钟与 UTC epoch 语义（存 UTC，与 REMINDER_TZ 无关）。

- [ ] **Step 11.3 CDP/人工真栈走查**（设计 §11 P0 验收口径，泓舟验收项）：
  1. HMI 说"两分钟后提醒我测试"→ 回读确认 + created 卡 + **右舞台切 agenda**；
  2. 两分钟后自动播报 + fired 卡琥珀脉冲 + 舞台该条高亮 + 「完成/稍后10分钟」按钮可点；
  3. "这周有什么安排" → multi 分组形态；"打开限速提醒" 仍走端侧车控（smoke_edge 口径）。

- [ ] **Step 11.4 接入 e2e 清单 + 提交**：`scripts/run_e2e.ps1` / `run_e2e.sh` 各加一行 `python test/e2e_reminder.py`（照 e2e_voice_loop 先例的写法）。

```bash
git add test/e2e_reminder.py scripts/run_e2e.ps1 scripts/run_e2e.sh
git commit -m "test(reminder): 真栈 e2e 闭环（创建→NATS 到点触达→列表/完成→清空确认，自清理）"
```

---

## Task 12：全量回归 + 知识面收尾

- [ ] **Step 12.1 全量回归**：

```bash
python -m pytest --import-mode=importlib -q 2>&1 | tail -3     # 相对 Task 0 基线只增不减、零失败
python test/smoke_edge.py                                       # 端侧 13/13（限速提醒无回归）
cd hmi && npm test && npm run build && cd ..
```

- [ ] **Step 12.2 知识面同步**（本仓惯例：状态行与证据同步，不堆叙事）：
  - `AGENTS.md` §4 加"智能提醒 Agent"行（写实测数字：单测 N、e2e 6/6、真栈验收项）。
  - `docs/design/README.md`：设计行状态改"✅ P0 已落地并真栈验证"；本实施计划行同步。
  - 设计文档头部状态改"P0 已落地（commit 散列）"。

- [ ] **Step 12.3 收尾提交**（不 push——泓舟红线）：

```bash
git add AGENTS.md docs/design
git commit -m "docs(reminder): P0 落地状态同步（AGENTS.md/design README/设计文档）"
```

---

## 计划自审记录（writing-plans Self-Review，已执行）

1. **Spec 覆盖**：设计 §5 数据模型→Task 2；§6 intent/manifest/route_hints→Task 4/5/10；§7 时间解析→Task 1；§8 调度→Task 3；§9 卡片/舞台双形态（D7）→Task 6/7/8；§10 权限（profile.*，无新 scope）→Task 4 manifest；§11 P0 清单逐项→Task 4-12；§13 验收命令→Task 11/12。**无遗漏**。追问续轮（REMINDER_PENDING）在 Task 5 测试 `test_create_resumes_pending_title` 固化。
2. **占位符扫描**：无 TBD/TODO/"适当处理"；所有代码步给完整文件或精确插入点+完整代码；唯一的"实现期收敛"是 route_hints 正则——已配 Task 10 实测收敛流程与失败处置（改 manifest 不改测试），非占位符。
3. **类型一致性**：`ReminderStore.list_split/claim_due/find_by_title/set_status/cancel_all` 与 Task 5 agent、Task 3 scheduler 调用一致；`ParsedTime(status,fire_at,display)`、`to_card_item(now,tz)`、卡片字段 `view/date_label/items/todos/context/actions[].send_text` 与 types.ts/Cards/Stage/e2e 断言逐一对齐；`REMINDERS_ACTIVE="reminders_active"`/`REMINDER_PENDING="reminder_pending"` 与测试里的 profile 键一致。
4. **已知风险前置**：时钟可测性（agent `_now_utc` 注入）、依赖闭包（Dockerfile 双层 pip）、"今晚八点"类用例的跑测时刻漂移（固定时钟解决）均已在任务内消化。

