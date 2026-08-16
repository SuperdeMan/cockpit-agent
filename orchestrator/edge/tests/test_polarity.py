"""指令极性维度（QA 卡 Q7 / I-039）。

端侧分类器是「对象 × 动作」的**正向**匹配——「车窗别开」命中对象词 `车窗` +
动作词 `开` → `window.open`，**「别」不是任何判据的输入**。用户说别开，车窗开了。

⚠ 这一族在 Q13 之前有一条是**假绿**：「音乐别停」分类器照样判 pause，只是
`classify()` 产的 `music.pause` 不在 `LOCAL_INTENTS`、被踢上云才没执行。
Q13 把两个出口收敛之后它落地了——**缺陷第一次诚实显形**（真栈 NG3
green-by-accident → 0/3）。这就是卡把 Q13 排在 Q7 前面的理由。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import classify, classify_structured, split_and_classify_any
from polarity import is_negated_directive


# ── 判据本身 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "车窗别开", "空调别关", "音乐别停", "别开车窗", "别把天窗打开",
    "先别开空调", "不要开窗", "不许锁车门", "甭开了",
])
def test_negated_directives(text):
    assert is_negated_directive(text) is True, text


@pytest.mark.parametrize("text", [
    # 正例：必须放行。**否定守卫最贵的失败是把正常指令一起挡掉**
    "打开车窗", "关闭空调", "把天窗打开", "音乐暂停",
    # 双重否定：真实语义是**要做**，挡它等于反向漏执行
    "别忘了关窗", "别忘记打开空调",
    # 取消挂起：那是 pending_cancel 的事（Q1-A 已收敛），这里认了会打架
    "不用了", "算了",
    # 否定词否定的是**别的动词**：「别催」不是「别打开」
    "别催我，把空调打开",
    # 约束词不是指令否定（nearby 的检索约束，归 G6）
    "不要太辣的餐厅",
])
def test_not_negated_directives(text):
    assert is_negated_directive(text) is False, text


# ── 分类器出口：负极性写操作**不产出本地意图** ────────────────────────────

@pytest.mark.parametrize("text,forbidden", [
    ("车窗别开", "window.open"),
    ("空调别关", "hvac.off"),
    ("音乐别停", "media.pause"),
    ("别把天窗打开", "sunroof.open"),
])
def test_negated_write_action_is_not_classified_locally(text, forbidden):
    """**不产出**，而不是产出反向意图。

    「车窗别开」的反向是 `window.close`——用户没让你关，他让你别开。
    把「别开」映射成「关」只是换了一个错误动作，而且是一个**有副作用**的。
    """
    assert classify_structured(text) is None, f"{text} 仍被判成可本地执行"
    got = (classify(text) or {}).get("name")
    assert got != forbidden, f"{text} → {got}"
    assert got is None


def test_positive_counterparts_still_classify():
    """反向对照——这一半和上一半一样重要（§4.3「反向验证要两头做」）。"""
    for text, want in (("打开车窗", "window.open"),
                       ("关闭空调", "hvac.off"),
                       ("暂停音乐", "media.pause"),
                       ("打开天窗", "sunroof.open")):
        assert (classify(text) or {}).get("name") == want, text


def test_query_collar_still_works():
    """同一道出口收口上原有的「问句不许被执行成写操作」不得被本批改坏。"""
    assert classify_structured("车窗最多能开多大") is None


# ── 复合句：逐段判极性 ────────────────────────────────────────────────────

def test_compound_sentence_keeps_only_the_real_directive():
    """NG4 原句：三段里只有中间那段是真指令。

    分段路径逐段过 `classify_structured`，于是极性守卫**天然逐段生效**——
    这正是把守卫放在分类器出口（而不是某条路径里）的收益。
    """
    parts = split_and_classify_any("车窗别开，空调关了，音乐别停")
    assert parts is not None
    by_text = {p.get("_raw_text"): p for p in parts}
    assert by_text["车窗别开"]["_needs_cloud"] is True
    assert by_text["音乐别停"]["_needs_cloud"] is True
    mid = by_text["空调关了"]
    assert mid["_needs_cloud"] is False
    assert mid["data"]["object"] == "aircon"
