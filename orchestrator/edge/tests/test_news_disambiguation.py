"""新闻查询与媒体播放的端侧消歧回归。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fast_intent import classify


def test_explicit_news_lookup_goes_to_online_news_instead_of_media_playback():
    result = classify("查一下今天人工智能行业的重要新闻")
    assert result is not None
    assert result["name"] == "info.news"


def test_explicit_listen_and_ambiguous_clip_keep_media_semantics():
    assert classify("我要听体育新闻")["name"] == "media.play"
    assert classify("来段新闻")["name"] == "media.play"
