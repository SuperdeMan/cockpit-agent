"""「现在几点 / 今天几号 / 今天星期几」——系统自己持有的事实问句，**全仓唯一判据**（2026-10-09）。

墙钟是系统持有的事实，这类问句不交给模型答（闲聊 badcase 2026-07-15：prompt 只锚日期时，模型编一个像真的时刻）。
消费方两个：

- 闲聊整句直答（`agents/chitchat`）；
- 内置时间工具 `datetime.parse`（`orchestrator/cloud/tools`）：规划器把问句派给它，却漏给槽或只给「now」时，
  按这一步的用户原话逐分句认问句（核心旅程 P06 槽为空；真实用户「what time is it now」→ `{"text": "now"}`）。

迁来之前两份各自演化：闲聊三条整句正则，工具一张精确匹配表，覆盖面已经不同（工具认「今日 / 日期」，闲聊不认）。
正则须**占据整句**（去礼貌前缀、引导语与语气尾词后锚 ^$），防劫持「明天几点有比赛」「几点提醒我」「伦敦现在几点」。
设计：docs/design/2026-10-09-clock-question-tool-input.md
"""
from __future__ import annotations

import re
from datetime import datetime

from runtime.clause_split import SPLIT_MARKERS

WEEKDAY = "一二三四五六日"

_PREFIX_RE = re.compile(r"^(请问|问一下|问下|那|哎|诶|嘿)+")
_SUFFIX = " 呀啊呢哦吧了嘛么？?。！!，,"
#: 引导语：「告诉我现在几点」「帮我看下今天几号」。只在时钟判据里去掉，身份问句（`bare_question`）不受影响。
_LEAD_RE = re.compile(r"^(?:你|再|也)?(?:告诉我|跟我说(?:一?下)?|(?:帮我)?(?:看(?:一?下|看)|查(?:一?下|查)|说(?:一?下|说)))")

_CLOCK_RE = re.compile(r"^(现在|当前)?(是)?几点(钟)?$|^(现在|当前)(的)?(是)?(什么)?时间(是多少|是几点)?$")
_DATE_RE = re.compile(r"^(今天|今日)(是)?(几号|多少号|几月几号|几月几日|什么日期)$|^(今天|今日)(的)?日期(是)?(多少|什么)?$")
_WEEK_RE = re.compile(r"^(今天|今日)(是)?(星期几|周几|礼拜几)$")
#: 英文只认锚定的三种问法；带地点的（「what is the time in London」）不认——换时区不是本判据能答的。
_EN_CLOCK_RE = re.compile(r"^(?:what time is it|what'?s the time|what is the time)(?: now| right now)?$")
_EN_DATE_RE = re.compile(r"^(?:what'?s|what is) (?:the date|today'?s date|the date today)$")
_EN_WEEK_RE = re.compile(r"^what day is (?:it|today)(?: today)?$")


def bare_question(text: str) -> str:
    """去礼貌前缀与语气尾词：「请问现在几点呀」→「现在几点」。"""
    return _PREFIX_RE.sub("", (text or "").strip()).strip(_SUFFIX)


def clock_question(text: str) -> str:
    """整句是不是时钟问句：返回 "time" / "date" / "weekday"，不是返回空串。"""
    t = _LEAD_RE.sub("", bare_question(text), count=1).strip(_SUFFIX)
    if not t:
        return ""
    if _CLOCK_RE.match(t):
        return "time"
    if _DATE_RE.match(t):
        return "date"
    if _WEEK_RE.match(t):
        return "weekday"
    en = " ".join(t.lower().replace("’", "'").strip(" ?!.,").split())
    if _EN_CLOCK_RE.match(en):
        return "time"
    if _EN_DATE_RE.match(en):
        return "date"
    if _EN_WEEK_RE.match(en):
        return "weekday"
    return ""


def clock_question_in(text: str) -> str:
    """逐分句找时钟问句（分句只用全仓唯一的 `SPLIT_MARKERS`）：「打开充电口，顺便告诉我今天几号」→ "date"。"""
    for clause in SPLIT_MARKERS.split(str(text or "")):
        kind = clock_question(clause)
        if kind:
            return kind
    return ""


def spoken_time(now: datetime) -> str:
    """口语化时刻：「下午2点27分」（0 分说「整」；0 点按惯例说凌晨12点）。"""
    h, m = now.hour, now.minute
    seg = ("凌晨" if h < 5 else "早上" if h < 9 else "上午" if h < 12
           else "中午" if h == 12 else "下午" if h < 18 else "晚上")
    h12 = h % 12 or 12
    return f"{seg}{h12}点" + ("整" if m == 0 else f"{m}分")


def clock_answer(kind: str, now: datetime) -> str:
    """按墙钟答时钟问句；kind 不认识返回空串。英文问句也答中文（同 Android N-01 的拒绝话术）。"""
    if kind == "time":
        return f"现在是{spoken_time(now)}。"
    if kind == "date":
        return f"今天是{now.year}年{now.month}月{now.day}日，星期{WEEKDAY[now.weekday()]}。"
    if kind == "weekday":
        return f"今天星期{WEEKDAY[now.weekday()]}，{now.month}月{now.day}日。"
    return ""
