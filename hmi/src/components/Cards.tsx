// 信息类 UI 卡片组件：天气 / 股票 / 新闻 / 搜索 / POI。
// 设计风格：深空座舱 HUD——半透明玻璃态 + 微光边框 + 渐变高光。
import type {
  UiCard, WeatherCard, ForecastCard, StockCard,
  NewsCard, SearchCard, PoiListCard, PoiDetailCard,
} from '../types'

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
  return (
    <div className="card card-weather">
      <div className="card-weather-main">
        <span className="card-weather-icon">{icon}</span>
        <div className="card-weather-temp">
          <span className="temp-value">{card.temp}</span>
          <span className="temp-unit">℃</span>
        </div>
      </div>
      <div className="card-weather-info">
        <div className="card-weather-city">{card.city}</div>
        <div className="card-weather-text">{card.text}</div>
        <div className="card-weather-detail">
          {card.feels_like && <span>体感 {card.feels_like}℃</span>}
          {card.humidity && <span>湿度 {card.humidity}%</span>}
          {card.wind_dir && <span>{card.wind_dir}{card.wind_scale}级</span>}
        </div>
      </div>
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

function StockCardView({ card }: { card: StockCard }) {
  const isUp = !card.change?.startsWith('-')
  const colorClass = isUp ? 'stock-up' : 'stock-down'
  const arrow = isUp ? '▲' : '▼'
  return (
    <div className={`card card-stock ${colorClass}`}>
      <div className="card-stock-header">
        <span className="card-stock-name">{card.name}</span>
        <span className="card-stock-symbol">{card.symbol}</span>
      </div>
      <div className="card-stock-price">{card.price}</div>
      <div className="card-stock-change">
        <span className="stock-arrow">{arrow}</span>
        <span>{card.change}</span>
        <span className="stock-pct">（{card.change_pct}）</span>
      </div>
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
