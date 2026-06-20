// 信息类 UI 卡片组件：天气 / 股票 / 新闻 / 搜索 / POI。
// 设计风格：深空座舱 HUD——半透明玻璃态 + 微光边框 + 渐变高光。
import type {
  UiCard, WeatherCard, ForecastCard, StockCard,
  NewsCard, SearchCard, PoiListCard, PoiDetailCard,
} from '../types'
import { buildKlineGeometry, priceDirection } from '../cardMath.mjs'

// ─── 天气图标映射 ───
const WEATHER_ICONS: Record<string, string> = {
  '晴': '☀️', '多云': '⛅', '阴': '☁️', '小雨': '🌦️', '中雨': '🌧️',
  '大雨': '🌧️', '暴雨': '⛈️', '雷阵雨': '⛈️', '小雪': '🌨️', '中雪': '🌨️',
  '大雪': '❄️', '雾': '🌫️', '霾': '😷', '沙尘暴': '🌪️',
}
function weatherIcon(text: string): string {
  for (const [k, v] of Object.entries(WEATHER_ICONS)) {
    if (text.includes(k)) return v
  }
  return '🌤️'
}

// ─── 卡片渲染入口 ───

export function CardRenderer({ card }: { card: UiCard }) {
  switch (card.type) {
    case 'weather': return <WeatherCardView card={card} />
    case 'forecast': return <ForecastCardView card={card} />
    case 'stock_quote': return <StockCardView card={card} />
    case 'news_list': return <NewsCardView card={card} />
    case 'search_list': return <SearchCardView card={card} />
    case 'poi_list': return <PoiListCardView card={card} />
    case 'poi_detail': return <PoiDetailCardView card={card} />
    default: return null
  }
}

// ─── 天气卡片 ───

function WeatherCardView({ card }: { card: WeatherCard }) {
  const icon = weatherIcon(card.text)
  const telemetry = [
    card.feels_like && { label: '体感', value: `${card.feels_like}℃` },
    card.humidity && { label: '湿度', value: `${card.humidity}%` },
    card.wind_dir && { label: '风况', value: `${card.wind_dir}${card.wind_scale ? `${card.wind_scale}级` : ''}` },
    card.visibility && { label: '能见度', value: `${card.visibility}km` },
    card.precip && { label: '降水', value: `${card.precip}mm` },
    card.pressure && { label: '气压', value: `${card.pressure}hPa` },
  ].filter(Boolean) as Array<{ label: string; value: string }>
  return (
    <div className="card card-weather weather-overview">
      <div className="weather-hero">
        <div className="card-weather-main">
          <span className="card-weather-icon">{icon}</span>
          <div className="card-weather-temp">
            <span className="temp-value">{card.temp}</span>
            <span className="temp-unit">℃</span>
          </div>
        </div>
        <div className="card-weather-info">
          <div className="card-weather-city">{card.city}</div>
          <div className="card-weather-text">{card.text || '天气数据更新中'}</div>
          {card.cloud && <div className="weather-cloud">云量 {card.cloud}%</div>}
        </div>
      </div>
      {telemetry.length > 0 && (
        <div className="weather-telemetry">
          {telemetry.map((item) => <div className="weather-chip" key={item.label}>
            <span>{item.label}</span><strong>{item.value}</strong>
          </div>)}
        </div>
      )}
      {!!card.forecast?.length && <div className="weather-forecast-rail">
        {card.forecast.slice(0, 3).map((day, index) => <div className="weather-mini-day" key={`${day.date}-${index}`}>
          <span>{index === 0 ? '今天' : day.date.slice(5)}</span>
          <i>{weatherIcon(day.text_day)}</i>
          <strong>{day.temp_low}°<em> / </em>{day.temp_high}°</strong>
          <small>{day.text_day}{day.precip && ` · ${day.precip}mm`}</small>
        </div>)}
      </div>}
      {(card.air_quality?.aqi || card.indices?.length) && <div className="weather-brief-row">
        {card.air_quality?.aqi && <div className="air-badge">
          <span>空气</span><strong>AQI {card.air_quality.aqi}</strong>
          <em>{card.air_quality.category || '—'}{card.air_quality.pm2p5 && ` · PM2.5 ${card.air_quality.pm2p5}`}</em>
        </div>}
        {!!card.indices?.length && <div className="weather-advice">
          {card.indices.slice(0, 2).map((item) => <span key={item.name}>{item.name} <b>{item.level}</b></span>)}
        </div>}
      </div>}
      {!!card.alerts?.length && <div className="weather-alert-callout">
        <span>⚠</span><strong>{card.alerts[0].level}色预警</strong><p>{card.alerts[0].title}</p>
      </div>}
      {card.update_time && card.update_time !== 'mock' && (
        <div className="card-meta">{card.update_time.replace('T', ' ').replace(/\+.*/, '')}</div>
      )}
    </div>
  )
}

// ─── 天气预报卡片 ───

function ForecastCardView({ card }: { card: ForecastCard }) {
  return (
    <div className="card card-forecast">
      <div className="card-header">{card.city} 未来{card.days.length}天</div>
      <div className="card-forecast-days">
        {card.days.map((d, i) => (
          <div key={i} className="forecast-day">
            <div className="forecast-date">{d.date.slice(5)}</div>
            <div className="forecast-icon">{weatherIcon(d.text_day)}</div>
            <div className="forecast-text">{d.text_day}</div>
            <div className="forecast-temp">
              <span className="temp-low">{d.temp_low}°</span>
              <span className="temp-sep">~</span>
              <span className="temp-high">{d.temp_high}°</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── 股票卡片 ───

function KlineChart({ card }: { card: StockCard }) {
  const candles = buildKlineGeometry(card.candles || [], 320, 154)
  if (!candles.length) return <div className="kline-empty">暂无可用日 K 数据</div>
  return <div className="kline-panel">
    <div className="kline-topline"><span>近 {candles.length} 日 K 线</span><span>{candles[candles.length - 1]?.date}</span></div>
    <svg className="kline-chart" viewBox="0 0 320 154" role="img" aria-label={`${card.name}近期日K线`}>
      {[0.2, 0.5, 0.8].map((ratio) => <line key={ratio} x1="16" x2="304" y1={12 + 130 * ratio} y2={12 + 130 * ratio} className="kline-grid" />)}
      {candles.map((candle) => <g key={candle.date}>
        <line x1={candle.x} x2={candle.x} y1={candle.highY} y2={candle.lowY} stroke={candle.color} strokeWidth="1.4" />
        <rect x={candle.x - candle.bodyWidth / 2} y={candle.bodyY} width={candle.bodyWidth} height={candle.bodyHeight} rx="1" fill={candle.color} />
      </g>)}
    </svg>
  </div>
}

function StockCardView({ card }: { card: StockCard }) {
  const direction = priceDirection(card.change)
  const colorClass = `stock-${direction}`
  const arrow = direction === 'up' ? '▲' : direction === 'down' ? '▼' : '—'
  const directionText = direction === 'up' ? '上涨' : direction === 'down' ? '下跌' : '平盘'
  return (
    <div className={`card card-stock ${colorClass}`}>
      <div className="card-stock-header">
        <span className="card-stock-name">{card.name}</span>
        <span className="card-stock-symbol">{card.symbol}</span>
        <span className="stock-market-tag">日线</span>
      </div>
      <div className="card-stock-price">{card.price}</div>
      <div className="card-stock-change">
        <span className="stock-arrow">{arrow}</span>
        <span>{directionText} {card.change}</span>
        <span className="stock-pct">（{card.change_pct}）</span>
      </div>
      <KlineChart card={card} />
      {card.market_time && card.market_time !== 'mock' && (
        <div className="card-meta">{card.market_time}</div>
      )}
    </div>
  )
}

// ─── 新闻卡片 ───

function NewsCardView({ card }: { card: NewsCard }) {
  return (
    <div className="card card-news">
      <div className="card-header">
        {card.topic ? `「${card.topic}」新闻` : '今日热点'}
      </div>
      {card.summary && <div className="summary-brief"><span>结论摘要</span><p>{card.summary}</p></div>}
      <div className="card-news-list">
        {card.items.map((item, i) => (
          <div key={i} className="news-item">
            <div className="news-index">{i + 1}</div>
            <div className="news-content">
              <div className="news-title">{item.title}</div>
              {item.summary && <div className="news-summary">{item.summary}</div>}
              <div className="news-meta">
                {item.source && <span className="news-source">{item.source}</span>}
                {item.publish_time && item.publish_time !== 'mock' && (
                  <span className="news-time">{item.publish_time.replace('T', ' ').replace(/\+.*/, '')}</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── 搜索卡片 ───

function SearchCardView({ card }: { card: SearchCard }) {
  return (
    <div className="card card-search">
      <div className="card-header">搜索「{card.query}」</div>
      {card.summary && <div className="summary-brief"><span>结论摘要</span><p>{card.summary}</p></div>}
      <div className="card-search-list">
        {card.items.map((item, i) => (
          <div key={i} className="search-item">
            <a className="search-title" href={item.url} target="_blank" rel="noopener noreferrer">
              {item.title}
            </a>
            {item.snippet && <div className="search-snippet">{item.snippet}</div>}
            {item.source && <div className="search-source">{item.source}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── POI 列表卡片 ───

function PoiListCardView({ card }: { card: PoiListCard }) {
  return (
    <div className="card card-poi">
      <div className="card-header">附近{card.keyword}</div>
      <div className="card-poi-list">
        {card.items.map((item, i) => (
          <div key={i} className="poi-item">
            <div className="poi-name">{item.name}</div>
            <div className="poi-info">
              {item.rating > 0 && <span className="poi-rating">★ {item.rating}</span>}
              {item.distance_km > 0 && <span className="poi-dist">{item.distance_km}km</span>}
            </div>
            {item.address && <div className="poi-addr">{item.address}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── POI 详情卡片 ───

function PoiDetailCardView({ card }: { card: PoiDetailCard }) {
  return (
    <div className="card card-poi-detail">
      <div className="poi-detail-name">{card.name}</div>
      {card.address && <div className="poi-detail-addr">📍 {card.address}</div>}
      <div className="poi-detail-row">
        {card.rating > 0 && <span>★ {card.rating}</span>}
        {card.category && <span>{card.category}</span>}
      </div>
    </div>
  )
}
