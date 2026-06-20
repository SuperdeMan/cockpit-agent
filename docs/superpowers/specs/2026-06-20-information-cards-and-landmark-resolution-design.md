# Information Cards and Landmark Resolution Design

**Status:** approved by direct implementation request (2026-06-20)

## Goal

Make information answers useful at a glance: weather exposes the data already available from QWeather, stock responses carry real daily OHLC data for a Chinese-market K-line, news and web search speak in conclusions rather than result lists, and visual landmark descriptions resolve to searchable POIs before navigation begins.

## Architecture

`InfoAgent` remains the owner of weather, market, search, and news aggregation. It expands its provider contracts instead of asking the planner to understand provider-specific payloads. The HMI continues to receive one `ui_card` through the existing Gateway → Cloud → Edge transport, so no proto or orchestrator change is required.

`NavigationAgent` preserves Amap as the authority that validates a destination. When literal POI lookup fails, it asks the existing LLM gateway to turn the visual description into a small ordered list of formal Chinese POI candidates, then searches each candidate in Amap. An LLM suggestion is never navigated to unless Amap returns a POI.

## Data contracts

### Weather overview

`info.weather` returns the existing `type: "weather"` card with:

- current temperature, condition, feels-like temperature, humidity, wind, precipitation, pressure, visibility, cloud cover, dew point, and update time;
- three daily forecasts with day/night condition, high/low temperature, precipitation, humidity, UV index, sunrise, and sunset;
- current AQI and PM2.5 when available;
- up to three lifestyle advisories and active weather alerts.

The current reading is required. Forecast, air, indices, and alerts are optional: failures omit only that section, preserving real data from successful QWeather endpoints. QWeather performs one city lookup per overview request, then fetches the downstream endpoints concurrently.

### Stock snapshot

`StockProvider.history(symbol, limit)` returns ordered daily `StockCandle` values (`date`, `open`, `high`, `low`, `close`, `volume`). `info.stock` adds those values to its `stock_quote` card while retaining the current quote contract. Tushare fetches a bounded recent daily window; Alpha Vantage and mock providers implement the same contract.

### Summaries

News and search cards gain a `summary` string. The agent passes fresh provider content to the LLM gateway with a source-grounded, concise-answer prompt. If the gateway is unavailable, the deterministic fallback composes a short unnumbered brief from snippets; it never emits a numbered title/link dump as the spoken response. Source items remain in the card for inspection.

### Landmark candidates

After literal Amap lookup returns no POI, the navigation agent asks the LLM for up to three formal POI names and searches each in Amap. It reports a resolved landmark only after a provider result; otherwise it asks the driver for another distinguishing clue. This supports descriptors such as a city's "bamboo-shoot-shaped building" without hard-coding a brittle nickname table.

## HMI design

The existing deep-space HUD stays intact. The weather card becomes a compact atmosphere panel: dominant condition/temperature, telemetry chips, three-day forecast rail, air-quality badge, advisory strip, and alert callout. The stock card becomes a market instrument panel: quote header, red-for-up / green-for-down direction treatment (Chinese-market convention), SVG OHLC K-line with price grid, and explicit arrow/text so color is not the only signal. News and search begin with a highlighted "结论摘要" panel before their source list.

No chart library is introduced; a small SVG renderer avoids a bundle dependency and gracefully renders a no-history state.

## Error handling

- Provider failures continue to use the established mock fallback for required base data.
- Optional weather endpoint failures do not replace successful real weather data.
- Stock history failure leaves the quote usable and renders no K-line.
- LLM synthesis/resolution failure uses safe fallback behavior: source-derived prose for information, no unvalidated navigation action for landmarks.

## Test strategy

- Provider tests assert rich QWeather parsing and multi-day Tushare OHLC parsing.
- Agent tests assert overview card sections, summary-first search/news output, chart data, and landmark candidate fallback/validation.
- HMI TypeScript build verifies the new card contract and SVG rendering compile.
- Existing navigation, info, smoke, and HMI tests guard regressions.
