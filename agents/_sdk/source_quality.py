"""信源质量分层（共享）——按域名权威给来源分档，供检索后重排。

动机：Exa/搜索引擎按**相关性**返回，不区分权威性——学术/官方文档与内容农场会平等进池子
（深调研实测「混少量内容农场」）。本模块给来源一个**权威档位**，调用方据此重排：把学术/官方/
百科上移、内容农场下沉，让最权威的源优先进合成材料（top-N）并拿到靠前的引用编号。

设计取舍：
- **只重排、不丢弃**（诚实优先）：低质源沉到末尾，仅当更优源不足以填满 top-N 时才会用到，
  绝不因「权威性」静默删掉唯一信源。
- **稳定排序**：同档保留调用方传入的原相对序（即搜索引擎的相关性序），权威性只在跨档时起作用。
- 注入式、零依赖：纯函数，不依赖任何 Agent / 网络，info 与 deep-research 共用。

档位：3=学术/官方/标准/百科；2=权威媒体/老牌科技；1=默认/未知；0=内容农场/低质聚合。
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

# 学术 / 官方文档 / 标准组织 / 百科（最高权威）。匹配「等于或为其子域」。
_TIER3 = {
    # 学术出版/预印本/检索
    "arxiv.org", "nature.com", "science.org", "sciencedirect.com", "springer.com",
    "link.springer.com", "ieee.org", "ieeexplore.ieee.org", "acm.org", "dl.acm.org",
    "ncbi.nlm.nih.gov", "semanticscholar.org", "researchgate.net", "ssrn.com",
    "mdpi.com", "frontiersin.org", "plos.org", "jstor.org", "biorxiv.org",
    "medrxiv.org", "cnki.net", "aclanthology.org", "openreview.net", "pnas.org",
    "cell.com", "wiley.com", "tandfonline.com",
    # 学术元数据/开放知识基础设施（研究文档 A+：Crossref/OpenAlex/DOAJ/Semantic Scholar）
    "crossref.org", "api.crossref.org", "openalex.org", "doaj.org",
    # 学术学会/出版社（化学/物理/光学/材料常见）
    "acs.org", "cas.org", "rsc.org", "aps.org", "aip.org", "iop.org",
    "optica.org", "osapublishing.org", "ametsoc.org",
    # 中文学术（期刊/院刊/出版平台/预印本）
    "engineering.org.cn", "mater-rep.com", "cip.com.cn", "scichina.com",
    "sciengine.com", "chinaxiv.org", "sciopen.com",
    # 标准/国际组织
    "who.int", "un.org", "iso.org", "ietf.org", "rfc-editor.org", "w3.org",
    "nist.gov", "europa.eu", "3gpp.org", "oasis-open.org",
    # 官方/开放数据与统计（研究文档 A+：World Bank/IMF/FRED/Eurostat/UN）
    "worldbank.org", "imf.org", "stlouisfed.org", "clinicaltrials.gov",
    # 官方技术文档/规范（含 AI 厂商官方 API 文档）
    "learn.microsoft.com", "cloud.google.com", "kubernetes.io", "pytorch.org",
    "tensorflow.org", "developer.mozilla.org", "docs.python.org", "python.org",
    "ai.google.dev", "platform.openai.com",
    # 百科 / 开放知识
    "wikipedia.org", "britannica.com", "scholarpedia.org",
    "wikimedia.org", "wikidata.org",
}

# 权威媒体 / 老牌科技（次高）。
_TIER2 = {
    "reuters.com", "bloomberg.com", "ft.com", "economist.com", "wsj.com",
    "nytimes.com", "theguardian.com", "bbc.com", "bbc.co.uk", "technologyreview.com",
    "wired.com", "arstechnica.com", "hbr.org", "forbes.com", "theverge.com",
    # 中文权威媒体 / 科技
    "caixin.com", "thepaper.cn", "36kr.com", "huxiu.com", "jiqizhixin.com",
    "leiphone.com", "infoq.cn", "infoq.com", "qbitai.com", "geekpark.net",
    "xinhuanet.com", "people.com.cn", "yicai.com", "cls.cn", "stcn.com",
    "chinadaily.com.cn", "news.cn", "scmp.com", "dw.com",
    # 行业媒体 / 垂直科技 / 行业协会（优于内容农场，弱于学术/官方）
    "eet-china.com", "ofweek.com", "jiemian.com", "ithome.com", "gasgoo.com",
    "nbd.com.cn", "d1ev.com", "cnbeta.com.tw", "caev.org.cn",
    # 厂商工程博客 / 开放知识与事件基础设施（研究文档 B：CNCF/GDELT/Common Crawl）
    "developers.googleblog.com", "openai.com", "anthropic.com",
    "cncf.io", "gdeltproject.org", "commoncrawl.org",
}

# 内容农场 / 低质聚合（最低；只下沉不丢弃）。保守，只列公认 SEO 农场/文库/问答垃圾。
_TIER0 = {
    "baijiahao.baidu.com", "zhidao.baidu.com", "wenku.baidu.com", "jingyan.baidu.com",
    "360doc.com", "docin.com", "doc88.com", "renrendoc.com", "book118.com",
    "csdn.net",   # 混杂 SEO 转载，低于官方文档（仅下沉，dev 主题无官方源时仍会用到）
}

# 顶级域级权威信号（教育/政府/国际域）。
_TLD3 = (".edu", ".gov", ".int", ".edu.cn", ".gov.cn", ".ac.cn", ".edu.hk",
         ".gov.hk", ".ac.uk", ".gov.uk", ".edu.au", ".gov.au")


def _domain(url: str) -> str:
    try:
        host = urlparse(url or "").netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _match(domain: str, suffixes) -> bool:
    """domain 等于其中之一，或为其子域（endswith '.'+suffix）。"""
    return any(domain == s or domain.endswith("." + s) for s in suffixes)


def domain_tier(url: str) -> int:
    """域名权威档位：3 学术/官方/百科 · 2 权威媒体 · 1 默认 · 0 内容农场。空/无法解析→1。"""
    d = _domain(url)
    if not d:
        return 1
    if _match(d, _TIER0):
        return 0
    if d.endswith(_TLD3) or d.startswith(("docs.", "developer.")) or ".readthedocs.io" in d:
        return 3
    if _match(d, _TIER3):
        return 3
    if _match(d, _TIER2):
        return 2
    return 1


def rerank_by_authority(items: list, key=None) -> list:
    """按域名权威**稳定**重排：档位降序，同档保留原相对序（=搜索相关性序）。

    key: 从元素取 URL 的函数；默认元素本身即 URL 字符串。只重排不增删，输入空→空。
    """
    get = key if key is not None else (lambda x: x)
    return [it for _, it in sorted(
        enumerate(items),
        key=lambda p: (-domain_tier(get(p[1]) or ""), p[0]))]


def rerank_fresh_authority(items: list, recency_days: int, key=None,
                           published_key=None, now=None) -> list:
    """时效敏感查询的重排：先分「是否在时效窗口内」，组内再按权威档位，同档保原序。

    rerank_by_authority 的时效变体，**仅当调用方判定查询时效敏感（recency_days>0）时使用**
    ——纯权威序会让旧的高权威源压过新的低权威源（榜单/比分/价格类混入历史数据）。
    published 缺失/不可解析视为窗口外（按字典序比较 ISO 前缀，与 latest_published 同口径；
    跨时区的小时级偏差被天级窗口吸收）。recency_days<=0 时退化为纯权威序。
    只重排、不增删（诚实优先，与 rerank_by_authority 同一取舍）。
    """
    if recency_days <= 0:
        return rerank_by_authority(items, key=key)
    get_url = key if key is not None else (lambda x: x)
    get_pub = published_key if published_key is not None else (
        lambda x: x.get("published", "") if isinstance(x, dict) else "")
    base = now or datetime.now(timezone.utc)
    cutoff = (base - timedelta(days=recency_days)).strftime("%Y-%m-%dT%H:%M:%S")

    def _in_window(it) -> bool:
        pub = str(get_pub(it) or "")[:19]
        return bool(pub) and pub >= cutoff

    return [it for _, it in sorted(
        enumerate(items),
        key=lambda p: (-int(_in_window(p[1])),
                       -domain_tier(get_url(p[1]) or ""), p[0]))]
