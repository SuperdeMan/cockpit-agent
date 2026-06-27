"""P2 新闻：个性化排序 + 主动早报触发 + 时效归一/质量重排的纯函数单测（不联网、不起 NATS）。"""
from datetime import datetime

from agents.info.src.agent import (InfoAgent, _news_interest_keywords,
                                   _rank_news_by_interest,
                                   _normalize_publish_time, _rank_news_quality)


def test_interest_keywords_strip_prefix_and_suffix():
    kws = _news_interest_keywords(
        ["用户关注人工智能", "喜欢看科技和财经新闻", "我对新能源汽车感兴趣"])
    assert "人工智能" in kws and "科技" in kws and "财经" in kws
    assert "新能源汽车" in kws                 # 尾缀「感兴趣」「新闻」已剥
    assert "用户" not in kws and "关注" not in kws and "新闻" not in kws


def test_rank_news_promotes_interest_matches():
    raw = [{"title": "A股大涨", "snippet": ""},
           {"title": "英伟达发布新AI芯片", "snippet": "人工智能算力"},
           {"title": "本地天气", "snippet": ""}]
    ranked, hit = _rank_news_by_interest(raw, ["人工智能", "科技"])
    assert ranked[0]["title"] == "英伟达发布新AI芯片"   # 命中置顶
    assert "人工智能" in hit


def test_rank_news_no_interests_keeps_order():
    raw = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    ranked, hit = _rank_news_by_interest(raw, [])
    assert [n["title"] for n in ranked] == ["A", "B", "C"] and hit == []


def test_normalize_publish_time_relative_to_absolute():
    now = datetime(2026, 6, 27, 12, 0, 0)
    assert _normalize_publish_time("3小时前", now).startswith("2026-06-27T09:")
    assert _normalize_publish_time("30分钟前", now).startswith("2026-06-27T11:3")
    assert _normalize_publish_time("2天前", now).startswith("2026-06-25T")
    assert _normalize_publish_time("昨天", now) == "2026-06-26T00:00:00"
    assert _normalize_publish_time("刚刚", now).startswith("2026-06-27T12:")


def test_normalize_publish_time_absolute_and_unparseable():
    now = datetime(2026, 6, 27, 12, 0, 0)
    assert _normalize_publish_time("2026-06-25T08:00:00") == "2026-06-25T08:00:00"
    assert _normalize_publish_time("2026-06-25 08:00") == "2026-06-25T08:00:00"
    assert _normalize_publish_time("2026-06-25") == "2026-06-25T00:00:00"
    assert _normalize_publish_time("6月25日", now) == "2026-06-25T00:00:00"
    assert _normalize_publish_time("06/25/2026, 08:00 AM", now) == "2026-06-25T00:00:00"
    assert _normalize_publish_time("") == "" and _normalize_publish_time("mock") == ""
    assert _normalize_publish_time("不是时间") == ""


def test_rank_news_quality_authority_then_recency():
    items = [
        {"title": "农场", "url": "https://baijiahao.baidu.com/s", "publish_time": "2026-06-27T11:00:00"},
        {"title": "新华旧", "url": "https://www.xinhuanet.com/a", "publish_time": "2026-06-25T08:00:00"},
        {"title": "新华新", "url": "https://www.xinhuanet.com/b", "publish_time": "2026-06-27T09:00:00"},
        {"title": "随机", "url": "https://blog.example.com/c", "publish_time": "2026-06-27T10:00:00"},
    ]
    out = [n["title"] for n in _rank_news_quality(items)]
    # tier2(新华) > tier1(随机) > tier0(农场)；同档(新华)按时间新→旧
    assert out == ["新华新", "新华旧", "随机", "农场"]


def test_has_drive_start():
    assert InfoAgent._has_drive_start([{"key": "gear", "new": "D"}]) is True
    assert InfoAgent._has_drive_start([{"key": "speed_kmh", "new": "30"}]) is True
    assert InfoAgent._has_drive_start([{"key": "speed_kmh", "new": "0"}]) is False
    assert InfoAgent._has_drive_start([{"key": "hvac_temp", "new": "24"}]) is False
    assert InfoAgent._has_drive_start([]) is False
