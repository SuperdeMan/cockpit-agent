"""真实 provider 端到端冒烟：直接用真实 key 调高德/和风/搜索/新闻/股票 API，验证集成与解析。

与全栈 e2e 不同，本测试**不需要 docker/LLM**，只验证 provider 代码能否正确调真实外部
API 并解析。无对应 key 时自动 skip（仿 test_asr_e2e.py）。关键断言会识破"静默回退 mock"
的假通过（名称含『示例』/update_time==mock）。

跑法（manifest runner 提供结果命名空间；凭证仍只从根 .env 读取）：
    python test/e2e_real_providers.py
和风支持 JWT（项目ID+凭据ID+Ed25519 私钥）或 API Key；高德用 AMAP_KEY。
搜索优先 AnySearch（ANYSEARCH_API_KEY），降级 Bing（BING_SEARCH_KEY）。
新闻用 SerpApi（SERPAPI_API_KEY）。股票用 Tushare（TUSHARE_TOKEN）。
全链路（经 Edge 网关 + LLM 规划 + Agent）另见：make up 后 python test/e2e_ws.py
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "test"), str(_ROOT / "gen" / "python")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from support.e2e import CaseRecorder

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


def _load_dotenv() -> None:
    """Load the repository's one supported runtime env without overwriting."""

    path = _ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(
        encoding="utf-8",
        errors="ignore",
    ).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ── 凭证检测 ────────────────────────────────────────────

AMAP_KEY = os.getenv("AMAP_KEY", "")
_HAS_JWT = bool(os.getenv("QWEATHER_PROJECT_ID") and os.getenv("QWEATHER_KEY_ID")
                and _load_qweather_private_key())
HAS_QWEATHER = bool(_HAS_JWT or os.getenv("QWEATHER_KEY"))
HAS_ANYSEARCH = bool(os.getenv("ANYSEARCH_API_KEY"))
HAS_BING = bool(os.getenv("BING_SEARCH_KEY"))
HAS_SEARCH = HAS_ANYSEARCH or HAS_BING
HAS_SERPAPI = bool(os.getenv("SERPAPI_API_KEY"))
HAS_TUSHARE = bool(os.getenv("TUSHARE_TOKEN"))


class ProviderResultPlugin:
    """Aggregate pytest collection and reports into one strict E2E result."""

    def __init__(self, recorder: CaseRecorder) -> None:
        self._recorder = recorder
        self._selected: list[str] = []
        self._outcomes: dict[str, tuple[str, str]] = {}
        self._active = False

    @staticmethod
    def _case_id(nodeid: str) -> str:
        raw = nodeid.rsplit("::", 1)[-1].split("[", 1)[0]
        prefix = "".join(
            char.lower()
            if char.isascii() and (char.isalnum() or char == "_")
            else "_"
            for char in raw
        ).strip("_")[:48] or "provider_case"
        digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}_{digest}"

    def pytest_sessionstart(self, session) -> None:
        del session
        self._recorder.__enter__()
        self._active = True

    def pytest_collection_modifyitems(self, session, config, items) -> None:
        del session, config
        self._selected = [item.nodeid for item in items]
        self._outcomes = {
            item.nodeid: ("pending", "")
            for item in items
        }

    def pytest_runtest_logreport(self, report) -> None:
        if report.nodeid not in self._outcomes:
            return
        current, _detail = self._outcomes[report.nodeid]
        if report.failed:
            self._outcomes[report.nodeid] = (
                "fail",
                f"{report.when} phase failed",
            )
        elif report.skipped and current != "fail":
            self._outcomes[report.nodeid] = (
                "skip",
                str(report.longrepr),
            )
        elif report.when == "call" and report.passed and current == "pending":
            self._outcomes[report.nodeid] = ("pass", "")

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        if not self._active:
            return
        original_exitstatus = int(exitstatus)
        saw_test_failure = any(
            status == "fail"
            for status, _detail in self._outcomes.values()
        )
        for nodeid in self._selected:
            status, detail = self._outcomes.get(
                nodeid,
                ("fail", "pytest did not report an outcome"),
            )
            case_id = self._case_id(nodeid)
            if status == "pass":
                self._recorder.pass_case(case_id)
            elif status == "skip":
                self._recorder.skip_case(
                    case_id,
                    "credential_unavailable",
                    detail,
                )
            else:
                self._recorder.fail_case(
                    case_id,
                    "provider_execution_failed"
                    if status == "fail"
                    else "result_protocol",
                    detail or "pytest did not execute the selected case",
                )

        session_error: tuple[str, str] | None = None
        if original_exitstatus in (2, 3, 4):
            code = {
                2: "pytest_interrupted_or_collection_error",
                3: "pytest_internal_error",
                4: "pytest_usage_error",
            }[original_exitstatus]
            session_error = (
                code,
                f"pytest session exited with status {original_exitstatus}",
            )
        elif not self._selected or original_exitstatus == 5:
            session_error = (
                "pytest_no_tests_collected",
                "pytest selected zero real-provider cases",
            )
        elif original_exitstatus == 1 and not saw_test_failure:
            session_error = (
                "pytest_session_error",
                "pytest exited with status 1 without a reported test failure",
            )
        elif original_exitstatus not in (0, 1):
            session_error = (
                "pytest_session_error",
                f"pytest session exited with status {original_exitstatus}",
            )

        if session_error is not None:
            code, detail = session_error
            self._recorder.fail_case("pytest_session", code, detail)

        self._recorder.__exit__(None, None, None)
        self._active = False
        recorder_exitstatus = self._recorder.exit_code()
        session.exitstatus = recorder_exitstatus


def main(argv: list[str] | None = None) -> int:
    recorder = CaseRecorder()
    plugin = ProviderResultPlugin(recorder)
    extra = list(argv or [])
    for value in extra:
        candidate = Path(value)
        if (
            value in {".", ".."}
            or value.endswith(".py")
            or "::" in value
            or candidate.exists()
        ):
            raise ValueError("additional pytest arguments must not add a test target")
    args = [__file__, "-q", "-s", *extra]
    return int(pytest.main(args, plugins=[plugin]))


# ── 高德（Amap）────────────────────────────────────────

@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_search_returns_real_pois():
    p = AmapPOIProvider(AMAP_KEY)
    res = asyncio.run(p.search("充电站", near=GeoPoint(lng=116.397428, lat=39.90923), limit=5))
    print(f"\n[高德] 找到 {len(res)} 个：{[r.name for r in res[:3]]}")
    assert res, "高德未返回 POI"
    first = res[0]
    assert "示例" not in first.name, "疑似回退 mock（名称含『示例』），检查 AMAP_KEY/POI_VENDOR"
    assert first.lat and first.lng, "POI 缺坐标"


@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_geocode_and_route():
    p = AmapPOIProvider(AMAP_KEY)
    out = asyncio.run(p.get_route(GeoPoint(address="北京站"), GeoPoint(address="北京西站")))
    print(f"\n[高德] 路线 {out['distance_km']}km / {out['duration_min']}min / {len(out['steps'])} 步")
    assert out["distance_km"] > 0, "路线距离应 > 0"
    assert out["steps"], "路线应有步骤"


@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_reverse_geocode():
    p = AmapPOIProvider(AMAP_KEY)
    pt = asyncio.run(p.reverse_geocode(116.397428, 39.90923))
    print(f"\n[高德逆地理] {pt.address}")
    assert pt.address, "逆地理应返回地址"


@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_poi_detail():
    p = AmapPOIProvider(AMAP_KEY)
    # 先搜索获取一个 POI ID
    res = asyncio.run(p.search("天安门", limit=1))
    if res:
        poi = asyncio.run(p.poi_detail(res[0].id))
        print(f"\n[高德详情] {poi.name} - {poi.address}")
        assert poi.name, "POI 详情应有名称"


@pytest.mark.skipif(not AMAP_KEY, reason="No AMAP_KEY configured")
def test_amap_place_provider_returns_rich_fields():
    """nearby 富数据 provider 真冒烟：搜真实餐厅，断言非 mock + 富字段落地（评分/人均/电话/营业时间至少其一）。"""
    from agents.nearby.src.providers.amap import AmapPlaceProvider
    from agents.nearby.src.providers.base import GeoPoint as PlaceGeo
    p = AmapPlaceProvider(AMAP_KEY)
    res = asyncio.run(p.search("美食", category="餐饮", near=PlaceGeo(lng=116.397, lat=39.908)))
    assert res, "高德未返回结果"
    first = res[0]
    print(f"\n[高德周边] {first.name} 评分={first.rating} 人均={first.cost} 电话={first.tel}")
    assert "示例" not in first.name, "疑似回退 mock（名称含『示例』），检查 AMAP_KEY/POI_VENDOR"
    # 富字段至少命中一项（高德对部分 POI 缺 business，故 any 而非 all）
    assert any([first.rating, first.cost, first.tel, first.open_today]), \
        "富字段全空——检查 show_fields=business,photos 是否生效"


# ── 和风（QWeather）────────────────────────────────────

@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_now_returns_real_weather():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()
    assert not isinstance(p, MockWeatherProvider), \
        "工厂回退到了 mock——检查 WEATHER_VENDOR/JWT(项目ID·凭据ID·私钥) 或 QWEATHER_KEY"
    w = asyncio.run(p.now("北京"))
    print(f"\n[和风] {w.city} {w.text} {w.temp}℃ 体感{w.feels_like}℃ "
          f"{w.wind_dir}{w.wind_scale}级 @ {w.update_time}")
    assert w.update_time and w.update_time != "mock", "疑似回退 mock"
    assert w.temp != "", "缺温度"


@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_forecast_returns_real_forecast():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()
    assert not isinstance(p, MockWeatherProvider)
    forecast = asyncio.run(p.forecast("北京", days=3))
    print(f"\n[和风预报] {len(forecast)} 天：{[(d.date, d.text_day, d.temp_low+'~'+d.temp_high+'℃') for d in forecast]}")
    assert len(forecast) > 0, "预报为空"
    assert forecast[0].date, "缺日期"


@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_indices_returns_real_indices():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()
    assert not isinstance(p, MockWeatherProvider)
    indices = asyncio.run(p.indices("北京"))
    print(f"\n[和风指数] {[(i.name, i.level) for i in indices]}")
    assert len(indices) > 0, "生活指数为空"


@pytest.mark.skipif(not HAS_QWEATHER, reason="No QWeather JWT/API-Key configured")
def test_qweather_air_quality_returns_real_aqi():
    os.environ["WEATHER_VENDOR"] = "qweather"
    p = build_weather_provider()
    assert not isinstance(p, MockWeatherProvider)
    try:
        aq = asyncio.run(p.air_quality("北京"))
    except Exception as e:
        if "403" in str(e) or "Forbidden" in str(e):
            pytest.fail(
                "和风空气质量 provider 返回 403；运行期 provider 错误不得记为 skip",
            )
        raise
    print(f"\n[和风空气质量] AQI {aq.aqi} {aq.category} PM2.5={aq.pm2p5} 首要{aq.primary_pollutant}")
    assert aq.aqi, "缺 AQI"
    assert aq.update_time and aq.update_time != "mock", "疑似回退 mock"


# ── 联网搜索（AnySearch / Bing）────────────────────────

@pytest.mark.skipif(not HAS_SEARCH, reason="No ANYSEARCH_API_KEY or BING_SEARCH_KEY configured")
def test_search_returns_real_results():
    p = build_search_provider()
    assert not isinstance(p, MockSearchProvider), \
        "工厂回退到了 mock——检查 ANYSEARCH_API_KEY 或 BING_SEARCH_KEY"
    res = asyncio.run(p.search("人工智能 最新进展", limit=3))
    vendor = "AnySearch" if HAS_ANYSEARCH else "Bing"
    print(f"\n[{vendor}] {len(res)} 条：{[r.title for r in res[:3]]}")
    assert res, "搜索结果为空"
    assert "示例" not in res[0].title, "疑似回退 mock"


# ── 新闻（SerpApi: Google + Baidu）────────────────────

@pytest.mark.skipif(not HAS_SERPAPI, reason="No SERPAPI_API_KEY configured")
def test_serpapi_returns_real_news():
    p = build_news_provider()
    assert not isinstance(p, MockNewsProvider), \
        "工厂回退到了 mock——检查 SERPAPI_API_KEY"
    items = asyncio.run(p.headlines("科技", limit=3))
    print(f"\n[SerpApi] {len(items)} 条：{[n.title for n in items[:3]]}")
    assert items, "新闻为空"
    assert "示例" not in items[0].title, "疑似回退 mock"


# ── 股票（Tushare）─────────────────────────────────────

@pytest.mark.skipif(not HAS_TUSHARE, reason="No TUSHARE_TOKEN configured")
def test_tushare_returns_real_quote():
    p = build_stock_provider()
    assert not isinstance(p, MockStockProvider), \
        "工厂回退到了 mock——检查 TUSHARE_TOKEN"
    q = asyncio.run(p.quote("600519"))
    print(f"\n[Tushare] {q.name} {q.symbol} {q.price} {q.change} ({q.change_pct}) @ {q.market_time}")
    assert q.price, "缺价格"
    assert q.market_time and q.market_time != "mock", "疑似回退 mock"


if __name__ == "__main__":
    raise SystemExit(main())
