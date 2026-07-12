"""TTS 入口 markdown 清理单测（与 agents/_sdk/grounding.strip_markdown_speech 配对口径）。"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers import _sentence_segments, _strip_md_tts


def test_strip_md_tts_basic():
    assert _strip_md_tts("**加粗**和`代码`") == "加粗和代码"
    assert _strip_md_tts("# 标题\n- 列表项\n> 引用") == "标题\n列表项\n引用"
    assert _strip_md_tts("详见[公告](https://x.com)") == "详见公告"
    assert _strip_md_tts("A | B") == "A ， B"          # 竖线转停顿，不念符号
    assert _strip_md_tts("纯文本不动。") == "纯文本不动。"


def test_sentence_segments_strip_md_after_assembly():
    """句子组装完成后剥（跨增量 ** 对已合并，剥不漏），TTS 永不合成星号。"""
    async def deltas():
        for d in ("**固态电", "池**能量密度更高。", "第二`句`也干净。"):
            yield d

    async def collect():
        return [seg async for seg in _sentence_segments(deltas())]

    segs = asyncio.run(collect())
    joined = "".join(segs)
    assert "*" not in joined and "`" not in joined
    assert segs[0] == "固态电池能量密度更高。"
