"""One deferred-condition rule for edge and cloud."""
from __future__ import annotations

import pytest

from runtime.deferred_condition import (COMPLETE_DEFERRED_CONDITION_RE, DEFERRED_CONDITION_RE,
                                        is_deferred_instruction)


@pytest.mark.parametrize("text", [
    "如果深圳今天不下雪，就把空调打开", "要是后排有人就把车窗关上", "温度低于20度时打开空调",
    "电量低于20%的时候打开节能模式", "车速超过60就关天窗", "打开空调，要是太冷再关掉",
    "如果冷的话打开座椅加热", "看看电量够不够开到杭州，不够就找个充电站", "if it rains then close the windows",
])
def test_conditions_are_deferred(text):
    assert is_deferred_instruction(text)


@pytest.mark.parametrize("text", ["打开空调", "帮我打开车窗和空调", "把温度调到20度", "车窗关上", "", None])
def test_plain_instructions_are_not(text):
    assert not is_deferred_instruction(text)


def test_the_planner_uses_this_rule_and_no_copy():
    from orchestrator.cloud import planning
    assert planning._DEFERRED_CONDITION_RE is DEFERRED_CONDITION_RE
    assert planning._COMPLETE_DEFERRED_CONDITION_RE is COMPLETE_DEFERRED_CONDITION_RE
