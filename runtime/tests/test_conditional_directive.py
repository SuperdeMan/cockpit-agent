"""条件指令整句仍按提问处理——首轮与 T2 的问句闸都靠它（2026-10-12）。

「如果深圳今天不下雪，就把空调打开」：首轮条件还没查，后件不许执行；T2 看过观察之后，后件派不派由再规划决定。2026-10-12 试过在 T2
对条件指令放行（`2085fc7a`）：再规划在前件为假时照样派出后件——核心旅程 D02「如果深圳今天下雪，就把空调打开」3/3 开了空调，
话术「深圳今天不会下雪……不过空调已经帮你打开了」。当天撤回。条件判定可靠之前，这类句子里的端侧写和需确认步一律按问句拦。

带「帮我 / 请」或以「若」起头的条件句目前不算提问，问句闸拦不住后件，见核心旅程 D04（设计
docs/design/2026-10-12-conditional-consequent-and-clock-clause.md）。
"""
from __future__ import annotations

import pytest

from runtime.question_shape import is_non_directive_question


@pytest.mark.parametrize("text", [
    "如果深圳今天不下雪，就把空调打开",   # D01
    "如果深圳今天下雪，就把空调打开",     # D02
    "要是降温了就把座椅加热打开",
    "如果深圳今天下雪，就把车窗打开",
    "假如深圳今天下雪，就把空调打开",
    "如果深圳今天下雪，把空调打开",
])
def test_a_conditional_instruction_is_still_a_question_for_the_guard(text):
    assert is_non_directive_question(text)
