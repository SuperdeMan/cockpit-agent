"""「记住…」祈使词（`runtime.memory_directive`）：句首判据与「这句话里有没有让记」从同一份词表派生。"""
from __future__ import annotations

import pytest

from runtime.memory_directive import asks_to_remember, directive_content, is_memory_directive


@pytest.mark.parametrize("text", ["记住我喜欢喝拿铁", "我喜欢喝拿铁，帮我记一下", "我老婆生日是五月二十号，记下来",
                                  "别忘了我不吃香菜", "请记着我女儿叫小满"])
def test_asks_to_remember_anywhere_in_the_sentence(text):
    assert asks_to_remember(text)


@pytest.mark.parametrize("text", ["我喜欢喝拿铁", "到我老婆生日那天早上八点提醒我订花", "我老婆生日是五月二十号", "", None])
def test_a_sentence_without_any_directive_does_not_ask(text):
    assert not asks_to_remember(text)


def test_the_leading_directive_is_unchanged():
    """句首判据（规划器的受话判定、会话约束）不受影响：句中的「记一下」不算句首祈使。"""
    assert is_memory_directive("记住，我女儿叫小满") and directive_content("记住，我女儿叫小满。") == "我女儿叫小满"
    assert not is_memory_directive("我喜欢喝拿铁，帮我记一下") and directive_content("我喜欢喝拿铁，帮我记一下") == ""
