"""推理模型 <think> 内联剥离单测（2026-07-12 真栈探针：仅 MiniMax 开思考泄漏）。

纯函数 strip_think_block + 流式状态机 ThinkStreamStripper，不打网络。
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers import ThinkStreamStripper, strip_think_block


def test_strip_think_block_removes_leading_block():
    raw = "<think>用户问天空为什么蓝，要简短。</think>\n\n因为蓝光散射得最厉害。"
    assert strip_think_block(raw) == "因为蓝光散射得最厉害。"
    # 前导空白 + 标签也剥
    assert strip_think_block("  \n<think>x</think>答案") == "答案"


def test_strip_think_block_noop_for_clean_and_mid_text():
    assert strip_think_block("正常回答。") == "正常回答。"
    assert strip_think_block("") == ""
    # 正文中间出现字面 <think>（转述场景）不动——只处理头部
    mid = "有人喜欢在回复里写 <think> 标签，这不该被剥。"
    assert strip_think_block(mid) == mid


def test_strip_think_block_unclosed_returns_empty():
    # 思考未闭合＝被 max_tokens 截断在思考里：无正文可用，诚实置空（调用方走空响应兜底）
    assert strip_think_block("<think>没想完就被截断了……") == ""


def _feed_all(chunks):
    s = ThinkStreamStripper()
    out = [s.feed(c) for c in chunks]
    out.append(s.flush())
    return "".join(x for x in out if x)


def test_stream_stripper_drops_think_across_chunks():
    # MiniMax 真实形态：<think>…</think>\n\n正文，且标签被任意拆包
    chunks = ["<th", "ink>用户问为什么", "天空是蓝的</thi", "nk>\n\n因为蓝光", "散射最厉害。"]
    assert _feed_all(chunks) == "因为蓝光散射最厉害。"


def test_stream_stripper_passes_normal_stream_immediately():
    s = ThinkStreamStripper()
    assert s.feed("因为") == "因为"          # 首包即判定非标记，零丢字
    assert s.feed("蓝光散射。") == "蓝光散射。"


def test_stream_stripper_angle_bracket_but_not_think():
    # "<3" 颜文字开头：判定为非 think 前缀后整段放流
    assert _feed_all(["<3 ", "喜欢你这个问题。"]) == "<3 喜欢你这个问题。"


def test_stream_stripper_flush_keeps_probe_residue_drops_unclosed():
    # 极短回复恰似前缀：flush 原样放出不丢字
    assert _feed_all(["<th"]) == "<th"
    # 未闭合思考：整段丢弃
    assert _feed_all(["<think>没想完就断流了"]) == ""
