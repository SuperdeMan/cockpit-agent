"""取消判定——**一份词表、两条语境规则**（QA 卡 Q1-A）。

在这之前，`wait_confirm` 与 `wait_slot` 各判各的：

- `wait_confirm` 走 `_confirm_reply` 的「词占据整句」规则（`len(t) <= len(k)+3`，
  防「第二天不要去长城」含「不要」被误判成取消）；
- `wait_slot` 走子串词表 + 复合余量续处理（§37 那批的产物）。

于是**「取消刚才解锁」6 字 > 2+3，在 `wait_confirm` 下判不出取消**，挂起一直活着
——QA I-046 的原文现象（第三次单独说「取消」才清除）。判据：**同一件事的两条分支，
修了一条没修另一条**，是「同一件事有两份实现迟早有一份是错的」在**分支**上的形态
（同 B5 `stream_state.py`「判定抄两份正是 B1 那个 bug 的成因」）。

**收敛不是把一条抄给另一条，是把两条的并集写成一份。** 直接让 `wait_confirm`
复用 `wait_slot` 那套会**换一个洞**：`不订/不付/先不/不了` 只在 `wait_confirm`
的词表里，`不要了/别提醒了` 只在 `wait_slot` 的词表里。所以这里按**歧义度**分两层，
两层同属一份词表：

- **STRONG（无歧义取消短语）**：句中出现即取消。「取消刚才解锁」由它接住。
- **WEAK（裸否定词）**：只在**占据整句**时才算取消。它们做子串会误伤
  （「第二天不要去长城」含「不要」、「我吃不了这么多」含「不了」），
  而这正是 `wait_confirm` 那条整句规则本来要防的东西——**规则保留，作用域收窄到该防的那半**。

两条语境规则同源于上面这一份词表：

- `detect_cancel`：**有挂起**时用。STRONG 子串 + WEAK 整句 + 复合余量续处理。
- `is_standalone_cancel`：**没有挂起**时用（`_is_bare_confirm_word`）。只认整句。
  ⚠ 这条**必须**保持严格：放宽了「取消当前导航」会被答成「当前没有待确认的操作」，
  而不是去规划——那是 QA Q4 位置闸同款的「客户端/前置闸替编排做意图判定」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── 唯一词表 ──────────────────────────────────────────────────────────
# STRONG：语义自足的取消短语，做子串安全（旅程 B5-1 的 `_SLOT_CANCEL_RE`）。
_STRONG_WORDS = ("取消", "不用了", "算了", "不需要了", "不要了",
                 "别设了", "别提醒了", "不设了")
# WEAK：裸否定词，只在占据整句时算取消（原 `_NO_WORDS` 里 STRONG 未覆盖的那些）。
_WEAK_WORDS = ("不用", "不要", "不订", "不付", "不了", "别订", "先不")
# 「占据整句」的松弛量：与 `_confirm_reply` 的否定侧逐字一致（词长 + 3）。
_WHOLE_SENTENCE_SLACK = 3

_STRONG_RE = re.compile("|".join(_STRONG_WORDS))

# 复合取消句的「实质余量」量具（EVA 三§3，2026-08-15 真栈实测）：「算了咖啡不买了，
# 先去加点油，但还是别迟到」命中取消词后整句被吞、4.6ms 直回「已取消」——后半句是
# 新请求。剥掉取消词/标点/语气尾后余量 ≥ _COMPOUND_MIN 字 = 复合句。
_STRIP_RE = re.compile(
    "|".join(_STRONG_WORDS) + r"|不买了|不去了"
    r"|[，。,、！!？?；;\s]|吧$|啦$")
_COMPOUND_MIN = 6


@dataclass(frozen=True)
class CancelDecision:
    """`cancelled=False` 时另外两个字段无意义（保持默认）。"""
    cancelled: bool = False
    remainder: str = ""
    compound: bool = False


_NOT_CANCEL = CancelDecision()


def _whole_sentence_hit(text: str, words: tuple[str, ...]) -> bool:
    """词近似占据整句（`len(t) <= len(k) + slack`），不做宽松子串包含。"""
    return any(k in text and len(text) <= len(k) + _WHOLE_SENTENCE_SLACK
               for k in words)


def is_standalone_cancel(text: str) -> bool:
    """**没有挂起**的语境：这句话是不是一句裸取消词？只认整句。"""
    t = str(text or "").strip().lower()
    if not t:
        return False
    return _whole_sentence_hit(t, _STRONG_WORDS + _WEAK_WORDS)


def detect_cancel(text: str) -> CancelDecision:
    """**有挂起**的语境：这句话是不是在取消挂起？复合句的余量是什么？

    - `cancelled=False`：不是取消，按各分支原有语义继续（确认/补槽/换话题）。
    - `cancelled=True, compound=False`：纯取消——清挂起、回「已为您取消」。
    - `cancelled=True, compound=True`：取消只作用于挂起，**余句按全新请求继续处理**。
    """
    t = str(text or "").strip()
    if not t:
        return _NOT_CANCEL
    lowered = t.lower()
    if not (_STRONG_RE.search(lowered)
            or _whole_sentence_hit(lowered, _WEAK_WORDS)):
        return _NOT_CANCEL
    remainder = _STRIP_RE.sub("", t)
    return CancelDecision(
        cancelled=True,
        remainder=remainder,
        compound=len(remainder) >= _COMPOUND_MIN,
    )
