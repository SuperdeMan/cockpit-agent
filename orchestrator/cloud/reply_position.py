"""「这句话只说了第几个」——位置性回复的**唯一判据**（W10 起住在 engine 的澄清分支，评审四轮 R4-02 收尾下沉到这里）。

三种形态：裸序数（「第二个 / 选第一个吧 / 第二家」）、纯数字（「2」）、「N 号」（「二号方案」）。句末标点先剥掉——ASR 常给「第二个。」
（真实 App 会话 `app-wyi61a` 就是带句号上的云：客户端的整句锚定改写没接住它）。

它只回答「第几个」，不回答「哪个问题 / 哪份列表的第几个」：归属由调用方按「最近那个提示」判——engine 的澄清分支
（`_clarify_is_latest_prompt`）、planning 的序数写步闸（最新那份候选）。零领域词；量词与 `candidate_query._ORDINAL_RE` 同一组。
"""
from __future__ import annotations

import re

from runtime.cntime import cn_int

_ORDINAL_RE = re.compile(
    r"(?:选|要|就)?\s*第\s*([一二三四五六七八九十\d]+)\s*(?:个|家|项|条|种|款|杯|份)?(?:吧|呢)?")
_NUMBER_RE = re.compile(r"\d{1,2}")
_HAO_RE = re.compile(r"([一二三四五六七八九十\d]+)\s*号(?:方案|选项)?")


def reply_position(text: str | None) -> int | None:
    """裸序数 / 纯数字 /「N 号」→ 第几项（1 起）；不是这三种形态 ⇒ None。选项 label / send_text 不在这里。"""
    t = str(text or "").strip().rstrip("。！!？?").strip()
    m = _ORDINAL_RE.fullmatch(t)
    if m:
        return cn_int(m.group(1))
    if _NUMBER_RE.fullmatch(t):
        return int(t)
    m = _HAO_RE.fullmatch(t)
    if m:
        return cn_int(m.group(1))
    return None
