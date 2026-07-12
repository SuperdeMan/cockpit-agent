"""信源权威分层 + 重排单测。"""
from datetime import datetime, timezone

from agents._sdk.source_quality import (domain_tier, rerank_by_authority,
                                        rerank_fresh_authority)


def test_domain_tier_academic_official_encyclopedia():
    assert domain_tier("https://arxiv.org/abs/2401.001") == 3
    assert domain_tier("https://en.wikipedia.org/wiki/Solid_state_battery") == 3
    assert domain_tier("https://www.tsinghua.edu.cn/x") == 3        # .edu.cn TLD
    assert domain_tier("https://www.nist.gov/x") == 3               # .gov TLD
    assert domain_tier("https://learn.microsoft.com/zh-cn/azure") == 3
    assert domain_tier("https://docs.anysite.com/guide") == 3       # docs.* 官方文档启发
    assert domain_tier("https://foo.readthedocs.io/en/latest") == 3
    # 中文学术：院刊/期刊/出版平台（含子域）
    assert domain_tier("https://www.engineering.org.cn/x") == 3
    assert domain_tier("https://www.mater-rep.com/x") == 3
    assert domain_tier("https://esst.cip.com.cn/article/1") == 3    # cip.com.cn 子域
    assert domain_tier("https://www.cas.org/x") == 3
    # 研究文档采纳：官方数据/统计/学术元数据/标准/AI 官方文档/.gov.hk
    assert domain_tier("https://api.worldbank.org/v2/x") == 3
    assert domain_tier("https://api.crossref.org/works") == 3
    assert domain_tier("https://api.openalex.org/works") == 3
    assert domain_tier("https://www.3gpp.org/x") == 3
    assert domain_tier("https://ai.google.dev/api") == 3
    assert domain_tier("https://www.news.gov.hk/x") == 3           # .gov.hk TLD


def test_domain_tier_reputable_media():
    assert domain_tier("https://www.reuters.com/tech") == 2
    assert domain_tier("https://36kr.com/p/123") == 2
    assert domain_tier("https://www.jiqizhixin.com/articles/x") == 2
    assert domain_tier("https://www.eet-china.com/x") == 2          # 行业媒体
    assert domain_tier("https://www.ofweek.com/x") == 2
    assert domain_tier("https://www.chinadaily.com.cn/x") == 2      # 研究文档采纳：权威媒体
    assert domain_tier("https://www.scmp.com/news/x") == 2
    assert domain_tier("https://cncf.io/blog/x") == 2


def test_domain_tier_content_farm_and_default():
    assert domain_tier("https://blog.csdn.net/u/article") == 0
    assert domain_tier("https://baijiahao.baidu.com/s?id=1") == 0
    assert domain_tier("https://wenku.baidu.com/view/x") == 0
    assert domain_tier("https://some-random-blog.xyz/post") == 1   # 默认
    assert domain_tier("https://baike.baidu.com/item/x") == 1      # 百度百科保持中立(不农场不权威)
    assert domain_tier("") == 1
    assert domain_tier("not a url") == 1


def test_rerank_stable_within_tier():
    items = [
        {"url": "https://blog.csdn.net/a"},     # 0
        {"url": "https://random1.com/b"},        # 1
        {"url": "https://arxiv.org/c"},          # 3
        {"url": "https://reuters.com/d"},        # 2
        {"url": "https://random2.com/e"},        # 1
    ]
    out = [s["url"] for s in rerank_by_authority(items, key=lambda s: s["url"])]
    assert out == [
        "https://arxiv.org/c",       # 3
        "https://reuters.com/d",     # 2
        "https://random1.com/b",     # 1（同档保留原序：random1 在 random2 前）
        "https://random2.com/e",     # 1
        "https://blog.csdn.net/a",   # 0 沉底
    ]


def test_rerank_empty_and_plain_string_key():
    assert rerank_by_authority([]) == []
    # 默认 key：元素本身即 URL
    out = rerank_by_authority(["https://x.com/1", "https://nature.com/2"])
    assert out[0] == "https://nature.com/2"


_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def test_rerank_fresh_window_beats_stale_authority():
    """时效敏感（recency_days>0）：窗口内的低权威新源排在窗口外的高权威旧源之前。"""
    items = [
        {"url": "https://arxiv.org/old", "published": "2026-06-01T00:00:00"},   # 3 档但过期
        {"url": "https://random.com/new", "published": "2026-07-12T08:00:00"},  # 1 档在窗口
        {"url": "https://reuters.com/new", "published": "2026-07-11T20:00:00"}, # 2 档在窗口
    ]
    out = [s["url"] for s in rerank_fresh_authority(items, 2, key=lambda s: s["url"], now=_NOW)]
    assert out == ["https://reuters.com/new",   # 窗口内，档位 2
                   "https://random.com/new",    # 窗口内，档位 1
                   "https://arxiv.org/old"]     # 窗口外沉后（哪怕 3 档）


def test_rerank_fresh_missing_published_counts_as_stale():
    items = [
        {"url": "https://nature.com/x", "published": ""},                        # 无时间→窗口外
        {"url": "https://random.com/y", "published": "2026-07-12T01:00:00"},
    ]
    out = [s["url"] for s in rerank_fresh_authority(items, 2, key=lambda s: s["url"], now=_NOW)]
    assert out[0] == "https://random.com/y"
    # 窗口外组内仍按权威序：nature(3) 若与其他窗口外比较不吃亏
    items.append({"url": "https://blog.csdn.net/z", "published": ""})
    out2 = [s["url"] for s in rerank_fresh_authority(items, 2, key=lambda s: s["url"], now=_NOW)]
    assert out2.index("https://nature.com/x") < out2.index("https://blog.csdn.net/z")


def test_rerank_fresh_zero_days_degrades_to_authority():
    items = [
        {"url": "https://random.com/a", "published": "2026-07-12T00:00:00"},
        {"url": "https://nature.com/b", "published": "2020-01-01T00:00:00"},
    ]
    out = [s["url"] for s in rerank_fresh_authority(items, 0, key=lambda s: s["url"], now=_NOW)]
    assert out[0] == "https://nature.com/b"     # recency<=0 → 纯权威序，与既有行为一致
