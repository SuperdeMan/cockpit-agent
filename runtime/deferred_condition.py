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
