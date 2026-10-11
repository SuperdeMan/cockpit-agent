"""Deferred conditions: does an instruction wait on something? Shared by edge and cloud.

In 「如果…就…」 or 「温度低于20度时…」 whether the second half runs depends on the
first. The cloud planner upgrades such plans to its bounded loop and never splits
the clauses. The edge must not run any part of them locally either: splitting the
sentence executes the consequent and drops the condition. The CA2-10 real-stack
probe (2026-10-02) saw 「如果深圳今天不下雪，就把空调打开」 split at the edge and
the air conditioning switched on unconditionally. This is the only copy of the rule.
"""
from __future__ import annotations

import re

DEFERRED_CONDITION_RE = re.compile(
    r"(?:如果|要是|假如|若|只要|除非|"
    r"(?:不够|不足|超过|低于|高于|达到|满足).{0,40}?(?:就|则|时|后|才)|"
    r"(?:根据|依据).{0,40}(?:结果|情况).{0,20}(?:决定|选择|判断)|"
    r"\bif\b|\bwhen\b|\bunless\b)",
    re.IGNORECASE,
)
COMPLETE_DEFERRED_CONDITION_RE = re.compile(
    r"(?:如果|要是|假如|若|只要|除非).{1,80}?(?:就|则|才|便|的话)"
    r"[^，,。；;！？!?\n]{1,80}"
    r"|\bif\b.{1,120}?\bthen\b.{1,120}",
    re.IGNORECASE,
)


def is_deferred_instruction(text) -> bool:
    """Any part of this utterance may wait on a condition, so nothing may run before it is judged."""
    return bool(DEFERRED_CONDITION_RE.search(str(text or "")))


#: 以假设框架起句、条件和后件都说出来的条件指令（「如果深圳今天下雪，就帮我把空调打开」）。后件做不做取决于前件，前件要看过观察
#: 才判得了；规划器会升成多轮，但再规划不会可靠地判断前件——2026-10-12 看到「今天不会下雪」仍派出开空调（核心旅程 D02 3/3）。
#: 云端问句闸据此不执行后件的副作用：不带「帮我 / 请」的说法整句已按提问拦，带了的（以及以「若」起句的）靠这一条（D04）。
#: 只认起句的框架：「打开空调，要是太冷再关掉」前一半是当下就要做的事，不在此列。
_LEADING_CONDITION_RE = re.compile(
    r"^(?:那么?|那就|嗯|你)?(?:如果|要是|假如|若|万一|假设)(?:说)?"
    r"(?P<condition>[^，,。；;！？!?\n]{1,40}?)"
    r"(?:的话|就|则|那么?|[，,])"  # 不收「便」：会把「如果方便的话」切成条件「方」
)
#: 只是客气、不是外部条件：「如果可以的话，帮我把空调打开」「要是方便的话帮我开下窗」「要是你能的话就把空调打开吧」。
_POLITE_CONDITION_RE = re.compile(r"(?:你|您)?(?:可以|方便|能|愿意|有空|不麻烦|不介意|允许|行|没问题)(?:的话)?")
#: 后件只是「就行 / 就好了」：说的是做到这样就够，不是另一件要等条件的事（「如果你能把空调打开就好了」）。
_NO_CONSEQUENT_RE = re.compile(r"(?:就|那就|那|则)?(?:行|好|可以|够了?|OK|ok)?[了啊吧呀呢的。！!？?…~]*")


def is_conditional_instruction(text) -> bool:
    """以假设框架起句、说出了真条件和后件：后件要等条件判过才能做（判不了就不做）。"""
    t = re.sub(r"\s+", "", str(text or ""))
    m = _LEADING_CONDITION_RE.match(t)
    if not m or _POLITE_CONDITION_RE.fullmatch(m.group("condition")):
        return False
    consequent = t[m.end():].lstrip("，,、：:")
    return bool(consequent) and not _NO_CONSEQUENT_RE.fullmatch(consequent)
