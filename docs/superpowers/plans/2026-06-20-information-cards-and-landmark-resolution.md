# Information Cards and Landmark Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver rich weather and stock cards, source-grounded summaries, and validated landmark-description navigation.

**Architecture:** Expand provider contracts at the Agent boundary, aggregate data in `InfoAgent`, and preserve the existing `ui_card` transport. `NavigationAgent` only creates a navigation action after Amap validates either the literal destination or an LLM-derived formal POI candidate.

**Tech Stack:** Python 3.12, pytest, QWeather, Tushare/Alpha Vantage, gRPC LLM gateway, React/TypeScript, CSS, inline SVG.

---

### Task 1: Add a resilient weather overview contract

**Files:**

- Modify: `agents/info/src/providers/base.py`
- Modify: `agents/info/src/providers/mock.py`
- Modify: `agents/info/src/providers/qweather.py`
- Modify: `agents/info/src/agent.py`
- Test: `agents/info/tests/test_qweather_provider.py`
- Test: `agents/info/tests/test_agent.py`

- [ ] **Step 1: Write failing overview tests**

```python
def test_overview_parses_extra_weather_data():
    overview = asyncio.run(p.overview("北京"))
    assert overview.now.visibility == "10"
    assert overview.forecast[0].uv_index == "6"
    assert overview.air_quality.aqi == "52"

def test_weather_card_contains_overview_sections():
    res = asyncio.run(run_handle(InfoAgent(), "info.weather", slots={"city": "北京"}))
    assert len(res.ui_card["forecast"]) == 3
    assert res.ui_card["air_quality"]["aqi"]
    assert res.ui_card["indices"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests/test_qweather_provider.py agents/info/tests/test_agent.py -q`

Expected: FAIL because `overview`, rich weather fields, and card sections do not exist.

- [ ] **Step 3: Implement the minimal overview model**

```python
@dataclass
class WeatherOverview:
    now: Weather
    forecast: list[ForecastDay] = field(default_factory=list)
    air_quality: AirQuality = field(default_factory=AirQuality)
    indices: list[LifeIndex] = field(default_factory=list)
    alerts: list[WeatherAlert] = field(default_factory=list)
```

Add `overview()` to `WeatherProvider` and all implementations. QWeather does one location lookup, then uses `asyncio.gather(..., return_exceptions=True)` to fetch current weather, 3-day forecast, air quality, indices, and alerts. The current reading remains required; failed optional calls become empty sections. Parse `precip`, `pressure`, `vis`, `cloud`, and `dew` in `Weather`, plus `precip`, `uvIndex`, `sunrise`, and `sunset` in `ForecastDay`. Update `InfoAgent._weather` to serialize those sections in the existing `weather` card.

- [ ] **Step 4: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests/test_qweather_provider.py agents/info/tests/test_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add agents/info/src/providers/base.py agents/info/src/providers/mock.py agents/info/src/providers/qweather.py agents/info/src/agent.py agents/info/tests/test_qweather_provider.py agents/info/tests/test_agent.py; git commit -m "feat: enrich weather overview data"`

### Task 2: Carry daily OHLC data from stock providers

**Files:**

- Modify: `agents/info/src/providers/base.py`
- Modify: `agents/info/src/providers/mock.py`
- Modify: `agents/info/src/providers/stock_tushare.py`
- Modify: `agents/info/src/providers/stock_quote.py`
- Modify: `agents/info/src/agent.py`
- Test: `agents/info/tests/test_tushare_provider.py`
- Test: `agents/info/tests/test_stock_provider.py`
- Test: `agents/info/tests/test_agent.py`

- [ ] **Step 1: Write failing history tests**

```python
def test_history_parses_ohlc_in_chronological_order():
    candles = asyncio.run(p.history("600519.SH", limit=2))
    assert [(c.date, c.open, c.high, c.low, c.close) for c in candles] == [
        ("20260619", "1860", "1880", "1850", "1870"),
        ("20260620", "1875", "1900", "1870", "1888"),
    ]

def test_stock_card_contains_candles():
    res = asyncio.run(run_handle(InfoAgent(), "info.stock", slots={"symbol": "茅台"}))
    assert len(res.ui_card["candles"]) >= 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests/test_tushare_provider.py agents/info/tests/test_stock_provider.py agents/info/tests/test_agent.py -q`

Expected: FAIL because no history method or `candles` field exists.

- [ ] **Step 3: Implement the bounded history API**

```python
@dataclass
class StockCandle:
    date: str = ""
    open: str = ""
    high: str = ""
    low: str = ""
    close: str = ""
    volume: str = ""

class StockProvider(ABC):
    @abstractmethod
    async def history(self, symbol: str, limit: int = 20, meta=None) -> list[StockCandle]: ...
```

Tushare requests one recent date window, parses reverse-chronological daily rows, and returns the requested chronological tail. Alpha Vantage uses `TIME_SERIES_DAILY`; the mock returns deterministic candles. `InfoAgent._stock` keeps quote mandatory, loads history best-effort, and serializes it into `stock_quote`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests/test_tushare_provider.py agents/info/tests/test_stock_provider.py agents/info/tests/test_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add agents/info/src/providers/base.py agents/info/src/providers/mock.py agents/info/src/providers/stock_tushare.py agents/info/src/providers/stock_quote.py agents/info/src/agent.py agents/info/tests/test_tushare_provider.py agents/info/tests/test_stock_provider.py agents/info/tests/test_agent.py; git commit -m "feat: expose stock kline history"`

### Task 3: Make news and search conclusion-first

**Files:**

- Modify: `agents/info/src/agent.py`
- Test: `agents/info/tests/test_agent.py`

- [ ] **Step 1: Write failing summarization tests**

```python
def test_news_returns_summary_not_numbered_headline_dump():
    res = asyncio.run(run_handle(InfoAgent(), "info.news", slots={"topic": "科技"}))
    assert res.ui_card["summary"]
    assert "1." not in res.speech

def test_search_fallback_is_a_brief_not_a_result_list():
    agent = InfoAgent()
    agent.llm.complete = _raise_gateway_error
    res = asyncio.run(run_handle(agent, "info.search", slots={"query": "人工智能"}))
    assert res.ui_card["summary"] == res.speech
    assert "为您搜索到" not in res.speech
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests/test_agent.py -q`

Expected: FAIL because news has no summary and search falls back to numbered results.

- [ ] **Step 3: Implement a shared source-grounded helper**

```python
async def _summarize(self, subject: str, source_lines: list[str], fallback_points: list[str]) -> str:
    prompt = "只能依据资料，先给结论，不列标题或链接，不超过四句。"
    try:
        answer = (await self.llm.complete([...], temperature=0.2, max_tokens=260)).strip()
        if answer and not answer.startswith("[mock]"):
            return answer
    except Exception as e:
        logger.warning("summary synthesis failed: %s", e)
    points = [p.strip().rstrip("。") for p in fallback_points if p.strip()]
    return f"关于「{subject}」，" + "；".join(points[:2]) + "。"
```

Call it for search snippets and news summaries. Both cards receive `summary`; speech uses the same conclusion while the item list remains inspectable source evidence.

- [ ] **Step 4: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests/test_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add agents/info/src/agent.py agents/info/tests/test_agent.py; git commit -m "fix: summarize news and web search answers"`

### Task 4: Resolve visual landmark descriptions before navigation

**Files:**

- Modify: `agents/navigation/src/agent.py`
- Test: `agents/navigation/tests/test_agent.py`

- [ ] **Step 1: Write failing resolution tests**

```python
def test_navigate_to_resolves_and_validates_visual_landmark():
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider({"深圳笋一样的建筑物": [], "华润大厦": [_poi("华润大厦")]})
    agent.llm.complete = _async_return('["华润大厦"]')
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "深圳笋一样的建筑物"}))
    assert res.actions[0]["payload"]["destination"] == "华润大厦"

def test_navigate_to_reasks_when_no_candidate_is_validated():
    agent = NavigationAgent()
    agent.poi = _ScriptedPoiProvider(default=[])
    agent.llm.complete = _async_return('["不存在的地标"]')
    res = asyncio.run(run_handle(agent, "navigation.navigate_to", slots={"destination": "像飞船的建筑"}))
    assert res.status == "need_slot"
    assert res.actions == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/navigation/tests/test_agent.py -q`

Expected: FAIL because navigation has no semantic candidate retry and creates an unvalidated fallback action.

- [ ] **Step 3: Implement the bounded resolution path**

```python
async def _find_destination(self, description: str, meta):
    results = await self.poi.search(description, limit=3, meta=meta)
    if results:
        return description, results
    for candidate in await self._landmark_candidates(description):
        results = await self.poi.search(candidate, limit=3, meta=meta)
        if results:
            return candidate, results
    return "", []
```

`_landmark_candidates` asks for at most three formal POI names in a strict JSON array, strips Markdown fences, rejects malformed output, and never creates a route from a suggestion that Amap cannot find. A no-match result returns `NEED_SLOT` with a request for city, district, or another landmark clue.

- [ ] **Step 4: Run the test to verify it passes**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/navigation/tests/test_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add agents/navigation/src/agent.py agents/navigation/tests/test_agent.py; git commit -m "fix: resolve fuzzy landmark destinations"`

### Task 5: Render cockpit-grade overview and K-line UI

**Files:**

- Modify: `hmi/src/types.ts`
- Modify: `hmi/src/components/Cards.tsx`
- Modify: `hmi/src/styles.css`

- [ ] **Step 1: Extend the TypeScript contracts**

```ts
export type StockCandle = { date: string; open: string; high: string; low: string; close: string; volume: string }
export type StockCard = { type: 'stock_quote'; name: string; symbol: string; price: string; change: string; change_pct: string; market_time: string; candles: StockCandle[] }
export type NewsCard = { type: 'news_list'; topic: string; summary: string; items: Array<{ title: string; summary: string; source: string; publish_time: string }> }
export type SearchCard = { type: 'search_list'; query: string; summary: string; items: Array<{ title: string; url: string; snippet: string; source: string }> }
```

- [ ] **Step 2: Implement card renderers and CSS**

Implement a pure inline-SVG `KlineChart` with padded high/low scaling and OHLC wick/body rendering. Use `#ff5b55` when close ≥ open, `#2fb37b` when close < open, and label direction with arrow/text. Upgrade weather with telemetry chips, a forecast rail, AQI, lifestyle advice, and alerts. Add a highlighted `结论摘要` block before news/search sources. Use only responsive CSS; add no dependency.

- [ ] **Step 3: Run HMI verification**

Run: `npm test; npm run build`

Expected: all tests pass and TypeScript build exits 0.

- [ ] **Step 4: Commit**

Run: `git add hmi/src/types.ts hmi/src/components/Cards.tsx hmi/src/styles.css; git commit -m "feat: enrich information cards with weather and kline"`

### Task 6: Verify the integrated change

**Files:** Verify only.

- [ ] **Step 1: Compile the changed Python code**

Run: `python -m py_compile agents/info/src/agent.py agents/info/src/providers/base.py agents/info/src/providers/mock.py agents/info/src/providers/qweather.py agents/info/src/providers/stock_tushare.py agents/info/src/providers/stock_quote.py agents/navigation/src/agent.py`

Expected: exit 0.

- [ ] **Step 2: Run the focused Python suites**

Run: `$env:PYTHONPATH = "$PWD;$PWD\gen\python"; python -m pytest --import-mode=importlib agents/info/tests agents/navigation/tests -q`

Expected: PASS.

- [ ] **Step 3: Run smoke and HMI checks**

Run: `python test/smoke_edge.py; Set-Location hmi; npm test; npm run build`

Expected: smoke reports `13/13`; HMI reports all tests passing and a successful build.

- [ ] **Step 4: Inspect whitespace and scope**

Run: `git diff main...HEAD --check; git status --short`

Expected: no whitespace errors and only intended implementation files changed.
