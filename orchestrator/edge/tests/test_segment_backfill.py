"""分段语义：省略回填与并列展开（QA 卡 Q7 的另外两个维度）。

端侧分段把整句按连词切开、**每段独立**丢给分类器。这条链上缺两样东西：

  · **跨段省略消解**（I-040）：「把空调打开**然后立刻关掉**」的第二段没有对象，
    判不出 → 整段标 `_needs_cloud` 上云，而云侧拿到的是一个无对象的碎片
    「立刻关掉」。**第一步执行了，第二步蒸发了。**
  · **并列对象**（I-005）：「前后风挡除雾都打开」**压根没被拆**（句里没有连词），
    走单句路径命中 `rear_defogger.open`——前风挡从头到尾没被表达过。
    「不是拆了丢一项，是没拆 + 单句只认一个对象。」

两个机制都**只用同一个分类器自己**做判据，不加第二份词表（B4）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import fast_intent as fi
from fast_intent import split_and_classify, split_and_classify_any


def _pairs(text):
    got = split_and_classify(text)
    return [(i["data"].get("object"), i["data"].get("operate")) for i in got] \
        if got else None


# ── 段内省略回填 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("把空调打开然后立刻关掉", [("aircon", "open"), ("aircon", "close")]),
    ("打开车窗，然后关掉", [("window", "open"), ("window", "close")]),
    ("打开天窗，然后关掉", [("sunroof", "open"), ("sunroof", "close")]),
])
def test_elided_object_is_backfilled_from_the_previous_segment(text, want):
    assert _pairs(text) == want


def test_backfill_never_invents_a_different_object():
    """安全闸②：拼上上一段的对象后解出了**别的**对象 ⇒ 这一段说的是别的事，不回填。"""
    assert _pairs("把空调打开然后放首歌") == [("aircon", "open"), ("music", "play")]


def test_trailing_modifier_is_not_turned_into_an_action():
    """安全闸③（**首版就是在这里发明了一个动作**）：「按顺序执行」是整句的修饰语，
    不是一条指令。首版把它回填成了第三个 `aircon.open`。

    整句因此走**混合路径**（前两段本地、修饰语上云），两个真动作都在。
    ⚠ 曾试过「把没有指令内容的段直接跳过，别把整句拖上云」，当场把
    「…播一首歌，**周杰伦的**」里的补语也丢了——见
    `test_no_content_segment_is_never_silently_dropped`。
    """
    assert _pairs("关闭空调然后打开，按顺序执行") is None   # 不全本地 ⇒ 走混合
    mixed = split_and_classify_any("关闭空调然后打开，按顺序执行")
    local = [(i["data"].get("object"), i["data"].get("operate"))
             for i in mixed if not i.get("_needs_cloud")]
    assert local == [("aircon", "close"), ("aircon", "open")]
    assert [i["_raw_text"] for i in mixed if i.get("_needs_cloud")] == ["按顺序执行"]


def test_no_content_segment_is_never_silently_dropped():
    """**「这一段没有指令内容」不等于「这一段可以丢」。**

    补语没有动作，但它带着用户说的东西：「打开空调，帮我播一首歌，**周杰伦的**」
    ——丢掉它端侧会随机放一首歌，用户点的歌没放成。整段上云只是慢一点，
    静默丢内容是答错。（这条是既有回归探针
    `test_trailing_qualifier_still_travels_with_its_head` 当场抓到的自伤。）
    """
    assert _pairs("打开空调，帮我播一首歌，周杰伦的") is None
    mixed = split_and_classify_any("打开空调，帮我播一首歌，周杰伦的")
    assert [i["_raw_text"] for i in mixed][-1] == "周杰伦的"
    assert mixed[-1]["_needs_cloud"] is True


def test_filler_probe_is_derived_not_hardcoded():
    """判「这一段里有没有动作」用的探针对象**从 commands.yaml 派生**。

    ⚠ 它必须是「裸词本身判不出意图」的那类对象。首版拿上一段的对象当探针，
    实测**把真的「打开」也判成了填充语**——「空调」这个裸词默认就解成 open，
    于是「空调打开」与「空调」逐字相同。这条断言钉的就是那个前提。
    """
    probe = fi._verb_probe()
    assert probe, "找不到合格探针 ⇒ 填充判定会整体失效（此时应当不跳过任何段）"
    assert fi.classify_structured(probe) is None
    assert fi._is_filler_segment("按顺序执行") is True
    assert fi._is_filler_segment("依次来") is True
    assert fi._is_filler_segment("打开") is False
    assert fi._is_filler_segment("立刻关掉") is False


def test_mixed_path_also_backfills():
    parts = split_and_classify_any("把空调打开然后立刻关掉")
    assert parts is not None
    assert [p["data"].get("operate") for p in parts] == ["open", "close"]
    assert all(not p.get("_needs_cloud") for p in parts)


# ── 并列对象展开 ──────────────────────────────────────────────────────────

def test_paired_front_rear_objects_are_expanded():
    assert _pairs("前后风挡除雾都打开") == [
        ("front_defogger", "open"), ("rear_defogger", "open")]
    assert _pairs("前后风挡除雾关掉") == [
        ("front_defogger", "close"), ("rear_defogger", "close")]


def test_same_object_pair_is_not_expanded():
    """反向闸：「前后座椅加热」两半都解成 `seat`——座椅是**一个**对象、前后是位置。
    拆开只会把一条指令变成两条重复指令。"""
    assert fi._expand_paired_objects("前后座椅加热都打开") == ["前后座椅加热都打开"]
    assert fi._expand_paired_objects("把前后车窗都打开") == ["把前后车窗都打开"]


def test_non_object_use_of_the_word_is_left_alone():
    for text in ("前面的车", "往前后退一点", "前后差不多"):
        assert fi._expand_paired_objects(text) == [text], text
