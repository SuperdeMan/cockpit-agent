"""一句话是不是在回答系统刚给出的候选（补槽建议 `SlotRequest.suggestions`）。

端侧快路径据此决定这一句要不要回到挂起所在的云端：用户点选或说出候选名时，候选名里碰巧带着车控 / 媒体词，
也不能被当成一条本地指令执行（2026-10-04 真栈：导航给出「上海东方明珠广播电视塔 / 东方·明珠城」两个候选，
点选第一个时端侧按「广播」把收音机打开了，云端那条追问根本没收到回答）。
判据只认候选名本身，零领域词：整句就是某个候选、是候选里连续的一段（至少 4 个字）、或候选之外只多了一两个字
（「去…」「…吧」）。候选只来自服务端本轮真实结果，客户端带不进来。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

_NOISE = re.compile(r"[\s·•,，、。.!！?？()（）\-—_/]")
_MIN_PART = 4      # 只说了候选的一段：至少 4 个字才算（「广播」两个字仍是一条指令）
_MAX_EXTRA = 2     # 候选之外多出来的字：「去」「就去」「吧」


def _key(text: str) -> str:
    return _NOISE.sub("", text or "")


def answers_offered_choice(text: str, suggestions: Iterable[str]) -> bool:
    """`text` 是对 `suggestions` 里某个候选的回答 ⇒ True。"""
    said = _key(text)
    if not said:
        return False
    for suggestion in suggestions or ():
        name = _key(str(suggestion))
        if not name:
            continue
        if said == name or (len(said) >= _MIN_PART and said in name) \
                or (name in said and len(said) - len(name) <= _MAX_EXTRA):
            return True
    return False
