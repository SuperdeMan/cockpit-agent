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


def test_rank_news_quality_recency_and_farm_sink():
    items = [
        {"title": "农场新", "url": "https://baijiahao.baidu.com/s", "publish_time": "2026-06-27T11:00:00"},
        {"title": "随机", "url": "https://blog.example.com/c", "publish_time": "2026-06-27T10:00:00"},
        {"title": "新华新", "url": "https://www.xinhuanet.com/b", "publish_time": "2026-06-27T09:00:00"},
        {"title": "新华旧", "url": "https://www.xinhuanet.com/a", "publish_time": "2026-06-25T08:00:00"},
    ]
    out = [n["title"] for n in _rank_news_quality(items)]
    # 非农场按时间新→旧(随机>新华新>新华旧)；内容农场(即使最新)沉底
    assert out == ["随机", "新华新", "新华旧", "农场新"]


def test_rank_news_quality_caps_per_source_for_diversity():
    items = [{"title": f"36氪{i}", "url": f"https://36kr.com/{i}", "source": "36氪",
              "publish_time": f"2026-06-27T{10 - i:02d}:00:00"} for i in range(5)]
    items.append({"title": "新华", "url": "https://news.cn/x", "source": "新华网",
                  "publish_time": "2026-06-27T06:00:00"})
    out = _rank_news_quality(items, per_source_cap=2)
    top3 = out[:3]
    assert sum(1 for n in top3 if "36kr.com" in n["url"]) <= 2   # 单一来源不刷屏
    assert any("news.cn" in n["url"] for n in top3)              # 其它来源被带入主区


def test_recent_only_drops_stale_keeps_unknown():
    from datetime import timedelta
    from agents.info.src.agent import _recent_only, _shanghai_now
    now = _shanghai_now()
    items = [{"title": "近", "publish_time": (now - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")},
             {"title": "旧", "publish_time": (now - timedelta(days=6)).strftime("%Y-%m-%dT%H:%M:%S")},
             {"title": "无时间", "publish_time": ""}]
    out = [n["title"] for n in _recent_only(items, days=3)]
    assert "近" in out and "无时间" in out and "旧" not in out   # 旧闻丢弃、无时间保留


def test_recent_only_all_stale_returns_original():
    from datetime import timedelta
    from agents.info.src.agent import _recent_only, _shanghai_now
    stale = (_shanghai_now() - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
    items = [{"title": "旧1", "publish_time": stale}, {"title": "旧2", "publish_time": stale}]
    assert len(_recent_only(items, days=3)) == 2   # 全旧→退回原列表，不至于无新闻


def test_clean_title_strips_source_suffix():
    c = InfoAgent._clean_title
    assert c("貨輪遭襲 長榮海運：人員均安 ｜ 公視新聞網 PNN") == "貨輪遭襲 長榮海運：人員均安"  # 全角｜长来源
    assert c("GPT设计GPT-36氪") == "GPT设计GPT"                  # 尾部「-36氪」
    assert c("新闻一|财经栏目") == "新闻一"                       # 半角|取首段
    assert c("不含分隔的正常新闻标题") == "不含分隔的正常新闻标题"


def test_clean_snippet_strips_markdown():
    from agents._sdk.grounding import clean_snippet
    assert clean_snippet("# 中东突变！") == "中东突变！"          # 行首标题#（对症摘要出现"# 标题"）
    assert clean_snippet("> 引用内容") == "引用内容"
    assert clean_snippet("- 列表项内容") == "列表项内容"
    assert clean_snippet("正文**加粗**与`代码`片段") == "正文加粗与代码片段"


def test_news_exa_livecrawl_only_for_topic():
    """话题新闻开 livecrawl=preferred（单调用无并发超时风险）；综合要闻不开（与 SerpApi 合并跑）。"""
    import asyncio

    captured = []

    class _SearchSpy:
        async def search(self, query, **kwargs):
            captured.append(kwargs)
            return []

    agent = InfoAgent()
    agent.search = _SearchSpy()
    asyncio.run(agent._news_from_exa("英伟达", 8, {}))
    asyncio.run(agent._news_from_exa("", 10, {}))
    assert captured[0].get("livecrawl") == "preferred"   # 话题态
    assert captured[1].get("livecrawl") == ""            # 综合态


def test_has_drive_start():
    assert InfoAgent._has_drive_start([{"key": "gear", "new": "D"}]) is True
    assert InfoAgent._has_drive_start([{"key": "speed_kmh", "new": "30"}]) is True
    assert InfoAgent._has_drive_start([{"key": "speed_kmh", "new": "0"}]) is False
    assert InfoAgent._has_drive_start([{"key": "hvac_temp", "new": "24"}]) is False
    assert InfoAgent._has_drive_start([]) is False
