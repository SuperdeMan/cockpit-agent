"""P2 新闻：个性化排序 + 主动早报触发的纯函数单测（不联网、不起 NATS）。"""
from agents.info.src.agent import (InfoAgent, _news_interest_keywords,
                                   _rank_news_by_interest)


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


def test_has_drive_start():
    assert InfoAgent._has_drive_start([{"key": "gear", "new": "D"}]) is True
    assert InfoAgent._has_drive_start([{"key": "speed_kmh", "new": "30"}]) is True
    assert InfoAgent._has_drive_start([{"key": "speed_kmh", "new": "0"}]) is False
    assert InfoAgent._has_drive_start([{"key": "hvac_temp", "new": "24"}]) is False
    assert InfoAgent._has_drive_start([]) is False
