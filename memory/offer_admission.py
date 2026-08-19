"""G7 询问式提醒建议的**准入判据**——唯一声明处（QA 卡 Q11 残余，2026-08-19）。

## 它挡的是什么

`_emit_event_offer` 原来的前提只有两条：`kind=episodic` 且 `event_time > now`
（`store.future_events`）。于是 QA 轮 I-014 的两种形态都能进来：

- **普通天气查询**被抽成一条 episodic，`event_time` 落在**次日 00:00**，
  用户收到一张「要到时候提前提醒你吗」——他只是问了句天气；
- 「接送信息陈述」同款。

卡 §3-Q11 给的口径是「**只在明确未来事件 + 时间可用时 offer**」。这份把那句话
变成三条确定性判据（零 LLM、可单测、两侧都验）。

## 判据与它们各自的理由

1. **时刻必须是用户说出来的，不是日期缺省填的。** 抽取 prompt 明写
   「只有日期没有时刻用 00:00:00」（`extract.py`），所以 `event_time_iso` 落在
   00:00 就等于「用户只说了个日子」。**一条「8月20日00:00提醒你」的建议卡本身
   就是坏的**——半夜零点提醒不是服务。
   ⚠ 这一条会连带漏掉真实的「下周五提车」（确实只有日期）。**这是刻意的**：
   同 §4.3「宁可漏一个告警，也不要对着一盏正常的灯劝人停车」——补时刻的正确做法
   是问用户，不是系统替他挑一个上午九点。
2. **至少提前一段时间。** 事件已经近在眼前时，「要不要到时候提醒你」是噪声不是服务。
3. **剥掉时间词之后还得剩下一件事。** offer 卡的标题就是这个剥完的串；剥完是空的
   说明这条记忆里除了时间什么都没有，**那就没有可提醒的事**。原实现在这里
   `or text` 回落成原文，等于把时间词又贴回标题里。

## 刻意没做的一条

**没有按 text 里的「查询/问了/搜了」这类词做排除。** 那是关键词排除，模型换个
转述就绕过去（§4.3 同一天自伤三次的那条）。判据取**形态**：有没有一个用户说出口的
时刻。天气查询之所以被挡住，不是因为它长得像查询，是因为它**没有时刻**。
"""
from __future__ import annotations

import os

#: 事件至少还有这么久才值得问「要不要提前提醒」。默认 30 分钟。
OFFER_MIN_LEAD_S = int(os.getenv("MEMORY_OFFER_MIN_LEAD_S", "1800"))


def _has_spoken_clock(iso: str) -> bool:
    """ISO 串里带的是**用户说出来的时刻**，还是日期缺省的 00:00？

    只看时刻段。缺时刻段（纯 `2026-08-22`）与 `T00:00[:00]` 一律判「没有时刻」。
    """
    s = (iso or "").strip()
    if "T" not in s and " " not in s:
        return False                      # 纯日期
    clock = s.replace(" ", "T").split("T", 1)[1]
    clock = clock.split("+")[0].split("Z")[0].strip()
    parts = clock.split(":")
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return False
    return (hh, mm) != (0, 0)


def admit_event_offer(event: dict, title: str, now_ts: int) -> tuple[bool, str]:
    """→ (放行?, 拒绝理由)。理由是给日志看的，不进话术。

    `title` 由调用方剥完时间词后传入——判据 3 判的就是它，**不在这里再剥一遍**
    （剥法只许有一份实现）。
    """
    try:
        ts = int(event.get("event_time") or 0)
    except (TypeError, ValueError):
        return False, "event_time 不是整数"
    if ts <= 0:
        return False, "没有 event_time"
    if not _has_spoken_clock(str(event.get("event_time_iso") or "")):
        return False, "只有日期没有时刻（抽取按约定填 00:00），不是可提醒的时刻"
    if ts - int(now_ts) < OFFER_MIN_LEAD_S:
        return False, f"距事件不足 {OFFER_MIN_LEAD_S} 秒，提前提醒没有意义"
    if not (title or "").strip():
        return False, "剥掉时间词后没有内容，这条记忆里没有可提醒的事"
    return True, ""
