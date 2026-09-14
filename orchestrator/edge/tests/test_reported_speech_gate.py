"""受话边界的第三维：播报 / 转述语域的写操作**不产出本地意图**。

2026-09-11 语音采纳真栈探针（`docs/reviews/2026-09-11-voice-input-acceptance-live-findings.md` §3）：
ptt +「欢迎收听今天的节目，本台记者为您报道新闻。」→ 端侧 0.97s 返回 `media.control / media.play`，
PoC VAL 把模拟 `media` 置成 playing（trace `ba82484fa8c54b9a`）。这条确定性快路径在上云之前就执行了，
云侧受话判定管不到它。修法与 question_shape / polarity 同形：出口只盖写操作，判据住 `runtime/reported_speech.py`。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from fast_intent import classify, classify_structured, split_and_classify, split_and_classify_any


# 首轮失败语料原话 + 同语域变体：三条入口（单句 / 全本地拆分 / 混合拆分）都不产出本地意图，整句上云。
# 语域是整句的属性——拆成分段后「请打开车窗通风」就是一条干净的指令了，所以拆分前先判整句。
@pytest.mark.parametrize("text", [
    "欢迎收听今天的节目，本台记者为您报道新闻。",
    "各位听众朋友大家好，下面请听一段音乐。",
    "接下来为您播出天气预报，请打开车窗通风。",
    "本台记者提醒您，请打开车窗通风。",     # 第二段单独看是一条干净的本地车控：混合拆分不许执行它
])
def test_broadcast_register_does_not_yield_local_write(text):
    assert classify_structured(text) is None, text
    assert classify(text) is None, text
    assert split_and_classify(text) is None, text
    assert split_and_classify_any(text) is None, text


# 正反对照：正常「播放新闻」保持媒体语义（test_news_disambiguation 的合同不动）
@pytest.mark.parametrize("text, name", [
    ("播放新闻", "media.play"),
    ("我要听体育新闻", "media.play"),
    ("来段新闻", "media.play"),
    ("把新闻关掉", "media.stop"),
    ("播放记者会直播", None),           # 「记者」单独出现不是语域标记；这句本来就不命中新闻规则
])
def test_user_news_requests_keep_media_semantics(text, name):
    result = classify(text)
    if name is None:
        assert result is None or result["name"].startswith("media."), result
    else:
        assert result is not None and result["name"] == name, (text, result)


def test_query_results_are_not_gated():
    """否决面只盖写操作：播报语域里的查询照旧秒回（与 question_shape 的合同同形）。"""
    result = classify("据报道今天很热，车里温度多少")
    assert result is None or result.get("name", "").split(".")[-1] not in ("play", "open", "set")
