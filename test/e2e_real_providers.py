"""真实 provider 端到端冒烟：直接用真实 key 调高德/和风 API，验证集成与解析。

与全栈 e2e 不同，本测试**不需要 docker/LLM**，只验证 provider 代码能否正确调真实外部
API 并解析。无对应 key 时自动 skip（仿 test_asr_e2e.py）。关键断言会识破"静默回退 mock"
的假通过（名称含『示例』/update_time==mock）。

跑法（把凭证写进 repo 根 .env，本测试会自动加载；无需 source）：
    python -m pytest test/e2e_real_providers.py -q -s
和风支持 JWT（项目ID+凭据ID+Ed25519 私钥）或 API Key；高德用 AMAP_KEY。
全链路（经 Edge 网关 + LLM 规划 + Agent）另见：make up 后 python test/e2e_ws.py
"""
import asyncio
import os

import pytest

from agents.navigation.src.providers.amap import AmapPOIProvider
from agents.navigation.src.providers.base import GeoPoint
from agents.info.src.providers import (
    build_weather_provider, build_search_provider,
    build_news_provider, build_stock_provider,
    _load_qweather_private_key,
)
from agents.info.src.providers.mock import (
    MockWeatherProvider, MockSearchProvider,
    MockNewsProvider, MockStockProvider,
)


def _load_dotenv():
    """最小 .env 加载（无 python-dotenv 依赖）：把 repo 根 .env 注入 os.environ（不覆盖已有）。
    妥善处理含空格/反斜杠的 Windows 路径值（不经 shell，不需要转义）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

AMAP_KEY = os.getenv("AMAP_KEY", "")
# 和风：JWT（项目ID+凭据ID+私钥）或 API Key 任一齐全即可真冒烟
_HAS_JWT = bool(os.getenv("QWEATHER_PROJECT_ID") and os.getenv("QWEATHER_KEY_ID")
                and _load_qweather_private_key())
HAS_QWEATHER = bool(_HAS_JWT or os.getenv("QWEATHER_KEY"))


@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_search_returns_real_pois():
    p = AmapPOIProvider(AMAP_KEY)
    # 北京天安门附近的充电站（周边搜索）
    res = asyncio.run(p.search("充电站", near=GeoPoint(lng=116.397428, lat=39.90923), limit=5))
    print(f"\n[高德] 找到 {len(res)} 个：{[r.name for r in res[:3]]}")
    assert res, "高德未返回 POI"
    first = res[0]
    assert "示例" not in first.name, "疑似回退 mock（名称含『示例』），检查 AMAP_KEY/POI_VENDOR"
    assert first.lat and first.lng, "POI 缺坐标"


@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_geocode_and_route():
    p = AmapPOIProvider(AMAP_KEY)
    # 地名先地理编码→坐标，再驾车路线
    out = asyncio.run(p.get_route(GeoPoint(address="北京站"), GeoPoint(address="北京西站")))
    print(f"\n[高德] 路线 {out['distance_km']}km / {out['duration_min']}min / {len(out['steps'])} 步")
    assert out["distance_km"] > 0, "路线距离应 > 0"
    assert out["steps"], "路线应有步骤"


@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_now_returns_real_weather():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()  # 工厂自动选 JWT/apikey
    assert not isinstance(p, MockWeatherProvider), \
        "工厂回退到了 mock——检查 WEATHER_VENDOR/JWT(项目ID·凭据ID·私钥) 或 QWEATHER_KEY"
    w = asyncio.run(p.now("北京"))
    print(f"\n[和风] {w.city} {w.text} {w.temp}℃ 体感{w.feels_like}℃ "
          f"{w.wind_dir}{w.wind_scale}级 @ {w.update_time}")
    assert w.update_time and w.update_time != "mock", \
        "疑似回退 mock，检查 QWEATHER_HOST/鉴权配置"
    assert w.temp != "", "缺温度"
    assert w.text != "", "缺天气现象"


@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_forecast_returns_real_forecast():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()
    assert not isinstance(p, MockWeatherProvider)
    forecast = asyncio.run(p.forecast("北京", days=3))
    print(f"\n[和风预报] {len(forecast)} 天：{[(d.date, d.text_day, d.temp_low+'~'+d.temp_high+'℃') for d in forecast]}")
    assert len(forecast) > 0, "预报为空"
    assert forecast[0].date, "缺日期"
    assert forecast[0].text_day, "缺白天天气"


@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_indices_returns_real_indices():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()
    assert not isinstance(p, MockWeatherProvider)
    indices = asyncio.run(p.indices("北京"))
    print(f"\n[和风指数] {[(i.name, i.level) for i in indices]}")
    assert len(indices) > 0, "生活指数为空"


# ── 联网搜索（Bing）──────────────────────────────────────

BING_KEY = os.getenv("BING_SEARCH_KEY", "")


@pytest.mark.skipif(not BING_KEY, reason="No BING_SEARCH_KEY configured")
def test_bing_search_returns_real_results():
    os.environ["SEARCH_VENDOR"] = "bing"
    p = build_search_provider()
    assert not isinstance(p, MockSearchProvider), \
        "工厂回退到了 mock——检查 SEARCH_VENDOR/BING_SEARCH_KEY"
    res = asyncio.run(p.search("人工智能 最新进展", limit=3))
    print(f"\n[Bing] {len(res)} 条：{[r.title for r in res[:3]]}")
    assert res, "搜索结果为空"
    assert "示例" not in res[0].title, "疑似回退 mock"


# ── 新闻（NewsAPI）──────────────────────────────────────

NEWS_KEY = os.getenv("NEWS_API_KEY", "")


@pytest.mark.skipif(not NEWS_KEY, reason="No NEWS_API_KEY configured")
def test_newsapi_returns_real_headlines():
    os.environ["NEWS_VENDOR"] = "newsapi"
    p = build_news_provider()
    assert not isinstance(p, MockNewsProvider), \
        "工厂回退到了 mock——检查 NEWS_VENDOR/NEWS_API_KEY"
    items = asyncio.run(p.headlines("", limit=3))
    print(f"\n[NewsAPI] {len(items)} 条：{[n.title for n in items[:3]]}")
    assert items, "新闻为空"
    assert "示例" not in items[0].title, "疑似回退 mock"


# ── 股票（Alpha Vantage）────────────────────────────────

STOCK_KEY = os.getenv("STOCK_API_KEY", "")


@pytest.mark.skipif(not STOCK_KEY, reason="No STOCK_API_KEY configured")
def test_stock_returns_real_quote():
    os.environ["STOCK_VENDOR"] = "quote"
    p = build_stock_provider()
    assert not isinstance(p, MockStockProvider), \
        "工厂回退到了 mock——检查 STOCK_VENDOR/STOCK_API_KEY"
    q = asyncio.run(p.quote("AAPL"))
    print(f"\n[Stock] {q.name} {q.price} {q.change} ({q.change_pct}) @ {q.market_time}")
    assert q.price, "缺价格"
    assert q.market_time and q.market_time != "mock", "疑似回退 mock"
