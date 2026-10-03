"""第三方文本的两条边界（CA2-17 S2）：能不能原样进用户原话、进模型提示时怎么标成资料。

**一、名字进按钮话术（I2）。** 按钮的 `send_text` 一被点下就是用户说的话——带着原话授权进规划、
分句和端侧快系统。商品 / 门店 / 分类的名字由第三方决定：名字里只要带一个分句标记（「，」
「然后」「并」…），后半截就会被当成用户自己的下一句命令。判据只看结构：
- 不含分句标记（与分句器同一张表 `runtime.clause_split.SPLIT_MARKERS`，不另抄）；
- 不含句末标点、换行、控制字符与看不见的格式字符（零宽、双向覆盖等）；
- 非空且有长度上限。
不认识任何领域词：名字里有没有「车窗」与这里无关——分不出第二句，就长不出第二句命令。
提示注入句式由规划入口的 `security.injection.detect_injection` 管，这里不重复。

**二、外部文本进提示（I3）。** 外部服务返回的内容进模型提示时包成资料区，并把内容里伪造的
区块标记压平——外部文本不能自己「关上」资料区、把后半段变成指令。
"""
from __future__ import annotations

import re
import unicodedata

from runtime.clause_split import SPLIT_MARKERS

#: 一个第三方名字（商品 / 门店 / 分类）的长度上限。
NAME_MAX = 48
#: 一句拼好的按钮话术的长度上限。
UTTERANCE_MAX = 96

_SENTENCE_END = re.compile(r"[。！？!?；;…]")
# Cc 控制字符（含换行、制表）、Cf 格式字符（零宽、双向覆盖）、Zl/Zp 行段分隔、Co 私用、Cs 代理、Cn 未分配。
_INVISIBLE = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})

REFERENCE_OPEN = "<reference-data>"
REFERENCE_CLOSE = "</reference-data>"
_REFERENCE_TAG = re.compile(r"<\s*/?\s*reference-data\s*>", re.IGNORECASE)


def _plain(text, limit: int) -> bool:
    if not isinstance(text, str):
        return False
    value = text.strip()
    if not value or len(value) > limit:
        return False
    if any(unicodedata.category(ch) in _INVISIBLE for ch in value):
        return False
    return not (_SENTENCE_END.search(value) or SPLIT_MARKERS.search(value))


def plain_name(text) -> bool:
    """第三方给的一个名字，能否原样拼进按钮话术。"""
    return _plain(text, NAME_MAX)


def plain_utterance(text) -> bool:
    """一句拼好的按钮话术，能否原样当用户原话发出。"""
    return _plain(text, UTTERANCE_MAX)


def as_reference(text) -> str:
    """把外部返回包成资料区；内容里自带的资料区标记先压平，免得外部文本提前关上区块。"""
    body = _REFERENCE_TAG.sub("[reference-data]", str(text or ""))
    return f"{REFERENCE_OPEN}\n{body}\n{REFERENCE_CLOSE}"
