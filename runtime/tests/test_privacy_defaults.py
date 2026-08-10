"""B3 prod 强制表第 10 项：S2S / 声纹 / 视觉的隐私默认挡位未被翻成「默认开」。

**为什么这一项是源码级断言而不是运行期 env 检查**：这三个开关的默认值只写在 HMI 的
``DEFAULT_SETTINGS`` 里，没有任何 env 能把它们翻成默认开——一条读 env 的运行期检查在
这里恒真，是**死检查**。判据同 shop 域零范例那次事故：**「能力从哪里声明」和「能力写在
哪个文件」是两件事**，检查要打在真正的声明处。

红线出处 CLAUDE.md §5：S2S 挡位上行原始音频、视觉抓帧、声纹识别都是**用户显式选择**才
成立的受控例外；把默认值翻成开，三个条件里的第一个当场失效。这道断言与
``orchestrator/cloud/tests/test_voiceprint_not_auth.py`` 同类——钉死的是不变量，不是行为。
"""
from __future__ import annotations

import os
import re

import pytest

_TYPES_TS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "hmi", "src", "types.ts")

#: 字段名 → 必须保持的默认值字面。
_OPT_IN_DEFAULTS = {
    "voicePipeline": "'classic'",   # 三段式；s2s 才上行原始音频
    "voiceprintEnabled": "false",
    "visionEnabled": "false",
    # 同族的唤醒/免唤醒（R4.3 opt-in）：唤醒前音频不离开浏览器，同样不许默认开。
    "handsFree": "false",
    "wakeWordEnabled": "false",
}


def _default_settings_block() -> str:
    with open(_TYPES_TS, "r", encoding="utf-8") as f:
        src = f.read()
    start = src.index("export const DEFAULT_SETTINGS")
    end = src.index("\n}", start)
    return src[start:end]


@pytest.mark.parametrize("field,expected", sorted(_OPT_IN_DEFAULTS.items()))
def test_privacy_sensitive_settings_default_off(field, expected):
    block = _default_settings_block()
    match = re.search(rf"^\s*{re.escape(field)}:\s*([^,\n]+),", block, re.M)
    assert match, f"DEFAULT_SETTINGS 里找不到 {field}——字段改名了就把这条断言一起改"
    assert match.group(1).strip() == expected, (
        f"{field} 默认值被改成 {match.group(1).strip()}；"
        "CLAUDE.md §5：S2S/视觉/声纹是用户显式选择才成立的受控例外，不许默认开")
