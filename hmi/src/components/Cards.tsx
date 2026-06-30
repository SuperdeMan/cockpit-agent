// 信息类 UI 卡片组件：天气 / 股票 / 新闻 / 搜索 / POI。
// 设计风格：深空座舱 HUD——半透明玻璃态 + 微光边框 + 渐变高光。
import { useState } from 'react'
import type {
  UiCard, WeatherCard, ForecastCard, StockCard,
  NewsCard, SearchCard, SearchAnswerCard, NewsDigestCard,
  SearchResultCard, NewsBriefCard, ResearchReportCard, SportsScoresCard, SportsScorersCard,
  RoutePlanCard, ChargingRouteCard, TripItineraryCard, PoiListCard, PoiDetailCard,
} from '../types'
import { airQualityBadge, buildKlineGeometry, priceDirection } from '../cardMath.mjs'
import { weatherAlertStatus, weatherAlertSummary } from '../weatherCard.mjs'
import { AQISection } from './aurora'

// AI 出品角标（照 A-4「AI · X」）：小极光点 + 虹彩文字，标识 AI 生成内容（§5）。
function AIBadge({ label }: { label: string }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 10, padding: '3px 11px 3px 5px', borderRadius: 999, background: 'rgba(91,140,255,0.10)', border: '1px solid rgba(91,140,255,0.20)' }}>
      <span style={{ width: 13, height: 13, borderRadius: '50%', background: 'var(--au-aurora-conic)', boxShadow: '0 0 8px rgba(91,140,255,0.5)' }} />
      <span className="au-aurora-text" style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.03em' }}>{label}</span>
    </div>
  )
}

// 当前电量进度条（照 A-5 充电路线卡）：soc 形如 "62%" → 解析为百分比 + 渐变填充。
function SocBar({ soc, dest }: { soc: string; dest: string }) {
  const pct = Math.max(0, Math.min(100, parseInt(soc, 10) || 0))
  const ok = pct > 50
  return (
    <div className="cr-soc">
      <div className="cr-soc-head"><span>当前电量</span><b className="au-num" style={{ color: ok ? 'var(--au-primary)' : 'var(--au-warn)' }}>{pct}%</b></div>
      <div className="cr-soc-track"><div className="cr-soc-fill" style={{ width: `${pct}%`, background: ok ? 'linear-gradient(to right,#46D6E0,#34D399)' : 'linear-gradient(to right,#F59E0B,#EF4444)' }} /></div>
      <div className="cr-soc-foot"><span>出发地</span><span>目的地 · {dest}</span></div>
    </div>
  )
}

// 卡内分节横线（照 A-3 HR）
const CardHR = () => <div style={{ height: 1, background: 'var(--au-line)' }} />

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

export function CardRenderer({ card, onAction }: { card: UiCard; onAction?: (text: string) => void }) {
  switch (card.type) {
    case 'card_group':
      // 多卡同屏：逐张渲染（如"查股价+新闻"→股票卡 + 新闻卡并存）
      return <>{((card as any).items || []).map((c: UiCard, i: number) =>
        <CardRenderer key={i} card={c} onAction={onAction} />)}</>
    case 'weather': return <WeatherCardView card={card} />
    case 'forecast': return <ForecastCardView card={card} />
    case 'stock_quote': return <StockCardView card={card} />
    case 'news_list': return <NewsCardView card={card} />
    case 'news_digest': return <NewsDigestCardView card={card} />
    case 'search_list': return <SearchCardView card={card} />
    case 'search_answer': return <SearchAnswerCardView card={card} />
    case 'search_result': return <SearchResultCardView card={card} />
    case 'news_brief': return <NewsBriefCardView card={card} />
    case 'research_report': return <ResearchReportCardView card={card} />
    case 'sports_scores': return <SportsScoresCardView card={card} />
    case 'sports_scorers': return <SportsScorersCardView card={card} />
    case 'route_plan': return <RoutePlanCardView card={card} />
    case 'charging_route': return <ChargingRouteCardView card={card} />
    case 'trip_itinerary': return <TripItineraryCardView card={card} onAction={onAction} />
    case 'poi_list': return <PoiListCardView card={card} />
    case 'poi_detail': return <PoiDetailCardView card={card} />
    default: return null
  }
}

// ─── 天气卡片 ───

function WeatherCardView({ card }: { card: WeatherCard }) {
  const alert = weatherAlertSummary(card.alerts)
  const upd = card.update_time && card.update_time !== 'mock'
    ? card.update_time.replace('T', ' ').replace(/\+.*/, '') : ''
  const tele: Array<{ icon: string; label: string; v: string | null; u: string }> = [
    { icon: '🌡', label: '体感', v: card.feels_like || null, u: '°C' },
    { icon: '💧', label: '湿度', v: card.humidity || null, u: '%' },
    { icon: '🌬', label: '风向', v: card.wind_dir ? `${card.wind_dir}${card.wind_scale ? `${card.wind_scale}级` : ''}` : null, u: '' },
    { icon: '👁', label: '能见度', v: card.visibility || null, u: 'km' },
    { icon: '🌧', label: '降水', v: card.precip || null, u: 'mm' },
    { icon: '📊', label: '气压', v: card.pressure || null, u: 'hPa' },
  ]
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* 预警 callout */}
      {alert && (
        <div style={{ padding: '10px 16px', background: 'rgba(245,158,11,0.11)', borderBottom: '1px solid rgba(245,158,11,0.20)', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <span style={{ color: 'var(--au-warn)', fontSize: 14, flexShrink: 0, lineHeight: 1.3 }}>⚠</span>
          <div>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--au-warn)' }}>{alert.headline}</div>
            <div style={{ fontSize: 11, color: 'var(--au-text-2)', marginTop: 2, lineHeight: 1.55 }}>{alert.detail}</div>
          </div>
        </div>
      )}
      {/* 头部：城市 + 大温度 + 天气文案 | 图标 + 更新 */}
      <div style={{ padding: '18px 18px 12px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>{card.city}</div>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, lineHeight: 1 }}>
            <span className="au-num" style={{ fontSize: 68, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--au-text)' }}>{card.temp}</span>
            <span className="au-num" style={{ fontSize: 24, fontWeight: 300, color: 'var(--au-text-2)', marginTop: 10 }}>°C</span>
          </div>
          <div style={{ fontSize: 13.5, color: 'var(--au-text-2)', marginTop: 5 }}>{card.text || '天气数据更新中'}</div>
        </div>
        <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 2 }}>
          <span style={{ fontSize: 44, lineHeight: 1 }}>{weatherIcon(card.text)}</span>
          {upd && <span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>更新 {upd}</span>}
        </div>
      </div>
      <CardHR />
      {/* telemetry 3×2 */}
      <div style={{ padding: '12px 13px', display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 7 }}>
        {tele.map((t) => {
          const miss = t.v == null
          return (
            <div key={t.label} style={{ padding: '9px 6px', borderRadius: 11, background: miss ? 'rgba(255,255,255,0.028)' : 'rgba(255,255,255,0.048)', border: '1px solid var(--au-line-2)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
              <span style={{ fontSize: 15 }}>{t.icon}</span>
              <span className="au-num" style={{ fontSize: 11.5, fontWeight: miss ? 400 : 600, color: miss ? 'var(--au-text-3)' : 'var(--au-text)', textAlign: 'center', lineHeight: 1.2 }}>{miss ? '—' : `${t.v}${t.u}`}</span>
              <span style={{ fontSize: 9.5, color: 'var(--au-text-3)' }}>{t.label}</span>
            </div>
          )
        })}
      </div>
      {/* 3 日预报 */}
      {!!card.forecast?.length && (
        <>
          <CardHR />
          <div style={{ padding: '13px 12px', display: 'flex' }}>
            {card.forecast.slice(0, 3).map((f, i) => (
              <div key={i} style={{ flex: 1, textAlign: 'center', padding: '0 6px', borderRight: i < 2 ? '1px solid var(--au-line)' : 'none' }}>
                <div style={{ fontSize: 11, color: 'var(--au-text-3)', marginBottom: 6 }}>{i === 0 ? '今天' : f.date.slice(5)}</div>
                <div style={{ fontSize: 28, marginBottom: 4, lineHeight: 1 }}>{weatherIcon(f.text_day)}</div>
                <div style={{ fontSize: 11, color: 'var(--au-text-2)', marginBottom: 8 }}>{f.text_day}</div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                  <span className="au-num" style={{ fontSize: 11, color: '#93C5FD' }}>{f.temp_low}°</span>
                  <div style={{ flex: 1, height: 2.5, borderRadius: 2, background: 'linear-gradient(to right,rgba(91,140,255,.5),rgba(255,165,50,.5))' }} />
                  <span className="au-num" style={{ fontSize: 11, color: '#FCA5A5' }}>{f.temp_high}°</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {/* AQI 7 档 */}
      {card.air_quality && (
        <>
          <CardHR />
          <div style={{ padding: '6px 16px 14px' }}><AQISection aqi={card.air_quality.aqi} category={card.air_quality.category} /></div>
        </>
      )}
      {/* 生活建议 2×2 */}
      {!!card.indices?.length && (
        <>
          <CardHR />
          <div style={{ padding: '13px 14px' }}>
            <div style={{ fontSize: 10.5, color: 'var(--au-text-3)', letterSpacing: '0.09em', fontWeight: 600, marginBottom: 10 }}>生活建议</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {card.indices.slice(0, 4).map((t) => (
                <div key={t.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 9, padding: '8px 10px', borderRadius: 11, background: 'rgba(255,255,255,0.04)', border: '1px solid var(--au-line-2)' }}>
                  <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{t.name}</span>
                  <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--au-text)' }}>{t.level}</span>
                </div>
              ))}
            </div>
          </div>
        </>
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

// 日K线（数据驱动 SVG，裸图——卡内自带标题/tabs）。红涨绿跌由 buildKlineGeometry 着色。
function KlineChart({ card }: { card: StockCard }) {
  const candles = buildKlineGeometry(card.candles || [], 320, 150)
  if (!candles.length) return null
  return (
    <svg style={{ display: 'block', width: '100%', height: 'auto' }} viewBox="0 0 320 150" role="img" aria-label={`${card.name}近期日K线`}>
      {[0.2, 0.5, 0.8].map((ratio) => <line key={ratio} x1="16" x2="304" y1={12 + 126 * ratio} y2={12 + 126 * ratio} className="kline-grid" />)}
      {candles.map((candle) => <g key={candle.date}>
        <line x1={candle.x} x2={candle.x} y1={candle.highY} y2={candle.lowY} stroke={candle.color} strokeWidth="1.4" />
        <rect x={candle.x - candle.bodyWidth / 2} y={candle.bodyY} width={candle.bodyWidth} height={candle.bodyHeight} rx="1" fill={candle.color} />
      </g>)}
    </svg>
  )
}

function StockCardView({ card }: { card: StockCard }) {
  const dir = priceDirection(card.change)
  const cc = dir === 'down' ? 'var(--au-down)' : dir === 'up' ? 'var(--au-up)' : 'var(--au-text-2)'
  const candles = card.candles || []
  const last = candles[candles.length - 1]
  const prev = candles[candles.length - 2]
  // 今开/最高/最低/昨收：从最后一根 K 线 + 前一根收盘推导（StockCard 无独立 OHLC 字段）
  const ohlc = last
    ? [{ l: '今开', v: last.open }, { l: '最高', v: last.high }, { l: '最低', v: last.low }, { l: '昨收', v: prev?.close ?? last.open }]
    : []
  const exch = card.symbol?.startsWith('6') ? '上证' : '深证'
  const stats = [
    { l: '市值', v: null }, { l: '市盈率', v: null }, { l: '市净率', v: null },
    { l: '成交量', v: last?.volume ?? null },
  ]
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* 头部 */}
      <div style={{ padding: '16px 18px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 5 }}>{card.name}</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="au-num" style={{ fontSize: 12, color: 'var(--au-text-3)' }}>{card.symbol}</span>
            <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>· {exch} · A 股主板</span>
          </div>
        </div>
        {card.market_time && card.market_time !== 'mock' && (
          <div style={{ padding: '3px 9px', borderRadius: 20, background: 'rgba(107,114,128,0.10)', border: '1px solid rgba(107,114,128,0.20)' }}>
            <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{card.market_time}</span>
          </div>
        )}
      </div>
      <CardHR />
      {/* 价格 + OHLC */}
      <div style={{ padding: '14px 18px 12px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
            <span className="au-num" style={{ fontSize: 40, fontWeight: 700, letterSpacing: '-0.025em', lineHeight: 1, color: cc }}>{card.price}</span>
            <span className="au-num" style={{ fontSize: 13, color: 'var(--au-text-3)' }}>CNY</span>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="au-num" style={{ padding: '3px 10px', borderRadius: 7, background: dir === 'down' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)', border: `1px solid ${cc}`, fontSize: 13, fontWeight: 700, color: cc }}>{card.change}</span>
            <span className="au-num" style={{ fontSize: 13, fontWeight: 600, color: cc }}>{card.change_pct}</span>
          </div>
        </div>
        {ohlc.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {ohlc.map((s) => (
              <div key={s.l} style={{ display: 'flex', gap: 14, justifyContent: 'space-between' }}>
                <span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>{s.l}</span>
                <span className="au-num" style={{ fontSize: 11.5, color: 'var(--au-text)' }}>{s.v}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <CardHR />
      {/* K 线 */}
      {candles.length ? (
        <div style={{ padding: '12px 4px 8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 14px', marginBottom: 8 }}>
            <span style={{ fontSize: 10.5, color: 'var(--au-text-3)', letterSpacing: '0.09em', fontWeight: 600 }}>日K线 · {candles.length}日</span>
            <div style={{ display: 'flex', gap: 10 }}>
              {['1日', '5日', '1月', '3月'].map((t, i) => (
                <span key={t} style={{ fontSize: 10.5, color: i === 1 ? 'var(--au-primary)' : 'var(--au-text-3)', fontWeight: i === 1 ? 600 : 400 }}>{t}</span>
              ))}
            </div>
          </div>
          <KlineChart card={card} />
        </div>
      ) : (
        <div style={{ padding: '22px 18px', textAlign: 'center', fontSize: 12, color: 'var(--au-text-3)' }}>K 线数据暂不可用</div>
      )}
      <CardHR />
      {/* 指标 4 列 */}
      <div style={{ padding: '13px 16px', display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '8px 6px' }}>
        {stats.map((s, i) => (
          <div key={i} style={{ textAlign: 'center' }}>
            <div className="au-num" style={{ fontSize: 12.5, fontWeight: 600, color: s.v ? 'var(--au-text)' : 'var(--au-text-3)', marginBottom: 4 }}>{s.v ?? '—'}</div>
            <div style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>{s.l}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── 新闻卡片（旧列表式，保留向后兼容）───

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

// ─── 新闻摘要卡片（ws2 摘要式）───

function NewsDigestCardView({ card }: { card: NewsDigestCard }) {
  return (
    <div className="card card-news-digest">
      <div className="card-header">📰 {card.topic || '今日热点'}</div>
      <div className="news-digest-summary">{card.summary}</div>
      {card.headlines.length > 0 && (
        <div className="news-digest-headlines">
          {card.headlines.map((h, i) => (
            <div key={i} className="headline-item">
              <span className="headline-dot">·</span>
              <span className="headline-title">{h.title}</span>
              {h.source && <span className="headline-source">{h.source}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 搜索卡片（旧列表式，保留向后兼容）───

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

// ─── 搜索答案卡片（ws2 结论式）───

function SearchAnswerCardView({ card }: { card: SearchAnswerCard }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="card card-search-answer">
      <div className="card-header">🔍 {card.query}</div>
      <div className="search-answer-text">{card.answer}</div>
      {card.sources.length > 0 && (
        <div className="search-answer-sources">
          <button className="sources-toggle" onClick={() => setExpanded(!expanded)}>
            ▸ {card.sources.length} 条来源
          </button>
          {expanded && (
            <div className="sources-list">
              {card.sources.map((s, i) => (
                <div key={i} className="source-item">
                  <a href={s.url} target="_blank" rel="noopener noreferrer">
                    {i + 1}. {s.title}
                  </a>
                  {s.source && <span className="source-domain">{s.source}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── 信息证据卡（2026-06-22 重设计）───
// 范式：气泡给结论（语音同步），卡片只承载证据——来源 / 关键数据 / 时效 / 置信度，
// 绝不复读结论文本。来源呈现全局统一：默认前 N 条，多余「更多」展开。

function relativeTime(iso?: string): string {
  if (!iso || iso === 'mock') return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 60000) return '刚刚'
  const min = Math.floor(diff / 60000)
  if (min < 60) return `${min}分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day}天前`
  return new Date(t).toLocaleDateString('zh-CN')
}

function CardHead({ icon, title, freshness }: { icon: string; title: string; freshness?: string }) {
  const rel = relativeTime(freshness)
  return (
    <div className="ev-head">
      <span className="ev-head-title">{icon} {title}</span>
      {rel && <span className="ev-fresh">⏱ {rel}</span>}
    </div>
  )
}

function ConfidenceBadge({ level }: { level?: string }) {
  if (!level) return null
  const map: Record<string, { label: string; tone: string }> = {
    high: { label: '高', tone: 'ok' },
    medium: { label: '中', tone: 'mid' },
    low: { label: '未充分核实', tone: 'low' },
  }
  const c = map[level] ?? map.medium
  return <div className={`ev-confidence ev-confidence-${c.tone}`}>置信度 <b>{c.label}</b></div>
}

function SourceList({ sources }: {
  sources: Array<{ title: string; url?: string; source?: string }>
}) {
  const [open, setOpen] = useState(false)
  if (!sources.length) return null
  const shown = open ? sources : sources.slice(0, 3)
  return (
    <div className="ev-sources">
      <div className="ev-sources-label">来源</div>
      <div className="ev-source-list">
        {shown.map((s, i) => (
          <div key={i} className="ev-source-item">
            <span className="ev-source-idx">{i + 1}</span>
            {s.url
              ? <a className="ev-source-title" href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a>
              : <span className="ev-source-title">{s.title}</span>}
            {s.source && <span className="ev-source-domain">{s.source}</span>}
          </div>
        ))}
      </div>
      {sources.length > 3 && (
        <button className="ev-more" onClick={() => setOpen(!open)}>
          {open ? '收起' : `更多 ${sources.length - 3} 条`}
        </button>
      )}
    </div>
  )
}

function SearchResultCardView({ card }: { card: SearchResultCard }) {
  return (
    <div className="card card-evidence">
      <AIBadge label="AI · 联网搜索" />
      <CardHead icon="🔍" title={card.query} freshness={card.freshness} />
      <SourceList sources={card.sources} />
      <ConfidenceBadge level={card.confidence} />
    </div>
  )
}

// 深度调研报告卡：气泡播一段式语音简报、卡片给可读分节报告（每节结论+引用+置信度）+ 未覆盖 gaps。
// 行车听简报、泊车展开读报告——首节默认展开，其余折叠（避免行车态一屏长文）。
const _CONF_CN: Record<string, string> = { high: '高', medium: '中', low: '低' }

function ResearchReportCardView({ card }: { card: ResearchReportCard }) {
  const [open, setOpen] = useState(false)
  const sections = card.sections || []
  const shown = open ? sections : sections.slice(0, 1)
  return (
    <div className="card card-evidence card-research">
      <AIBadge label="AI · 深度调研报告" />
      <CardHead icon="📑" title={card.question || '深度调研'} freshness={card.freshness} />
      {card.summary && (
        <div style={{ padding: '11px 14px', borderRadius: 12, background: 'rgba(70,214,224,0.07)', border: '1px solid rgba(70,214,224,0.16)', margin: '10px 0', fontSize: 13, lineHeight: 1.7, color: 'var(--au-text)' }}>
          {card.summary}
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <ConfidenceBadge level={card.overall_confidence} />
        {card.sources?.length ? <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>· 引用 {card.sources.length} 篇</span> : null}
      </div>
      <div style={{ marginTop: 10 }}>
        {shown.map((s, i) => (
          <div key={i} style={{ marginBottom: 10 }}>
            {s.heading && (
              <div style={{ fontWeight: 600, marginBottom: 2 }}>
                {s.heading}
                {s.confidence && (
                  <span style={{ opacity: 0.6, fontWeight: 400, fontSize: '0.85em' }}>
                    {' '}· 置信度{_CONF_CN[s.confidence] ?? s.confidence}
                  </span>
                )}
              </div>
            )}
            <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>{s.body}</div>
            {!!(s.citations && s.citations.length) && (
              <div style={{ marginTop: 3, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {s.citations.map((c) => <span key={c} className="ev-source-idx">{c}</span>)}
              </div>
            )}
          </div>
        ))}
      </div>
      {sections.length > 1 && (
        <button className="ev-more" onClick={() => setOpen(!open)}>
          {open ? '收起报告' : `展开完整报告（共 ${sections.length} 节）`}
        </button>
      )}
      {!!(card.gaps && card.gaps.length) && (
        <div style={{ marginTop: 8, opacity: 0.75, fontSize: '0.9em' }}>
          ⚠ 资料未充分覆盖：{card.gaps.join('；')}
        </div>
      )}
      <SourceList sources={card.sources} />
    </div>
  )
}

function NewsBriefCardView({ card }: { card: NewsBriefCard }) {
  const [open, setOpen] = useState(false)
  // 来源/原文链接默认全部折叠：第一性原理——车上用户扫标题+摘要即可，基本不会点原文链接，
  // 来源域名一行会喧宾夺主。折叠态只用「参考来源 N 个」简单标记数量，点击才展开。
  const [showSrc, setShowSrc] = useState(false)
  const srcCount = new Set(card.items.map((n) => n.source).filter(Boolean)).size
  const shown = open ? card.items : card.items.slice(0, 10)
  return (
    <div className="card card-evidence">
      <AIBadge label="AI · 新闻速览" />
      <CardHead icon="📰" title={card.topic || '今日值得关注'} freshness={card.freshness} />
      <ol className="ev-news-ol">
        {shown.map((n, i) => {
          // 来源名 + 相对时间默认常显（对症「看不到摘要/时间」）；「参考来源」折叠只控制来源是否变可点链接。
          const rel = relativeTime(n.publish_time)
          return (
            <li key={i} className="ev-news-li">
              <span className="ev-news-h">{n.title}</span>
              {n.summary && <div className="ev-news-sum">{n.summary}</div>}
              {(n.source || rel) && (
                <span className="ev-news-src">
                  {showSrc && n.url
                    ? <a href={n.url} target="_blank" rel="noopener noreferrer">{n.source}</a>
                    : n.source}
                  {rel ? (n.source ? ' · ' : '') + rel : ''}
                </span>
              )}
            </li>
          )
        })}
      </ol>
      <div className="ev-news-actions">
        {srcCount > 0 && (
          <button className="ev-more ev-src-toggle" onClick={() => setShowSrc(!showSrc)}>
            {showSrc ? '收起来源' : `参考来源 ${srcCount} 个`}
          </button>
        )}
        {card.items.length > 10 && (
          <button className="ev-more" onClick={() => setOpen(!open)}>
            {open ? '收起' : `更多 ${card.items.length - 10} 条`}
          </button>
        )}
      </div>
    </div>
  )
}

function FixtureRow({ f }: { f: SportsScoresCard['fixtures'][number] }) {
  const scored = (f.status === 'live' || f.status === 'finished') &&
    (f.home_goals !== '' || f.away_goals !== '')
  const kickoff = f.kickoff && f.kickoff.includes('T') ? f.kickoff.slice(11, 16) : ''
  return (
    <div className="fx-item">
      <div className={`fx-row fx-${f.status}`}>
        <span className="fx-team fx-home">
          <span className="fx-name">{f.home}</span>
          {f.home_logo && <img className="fx-flag" src={f.home_logo} alt="" loading="lazy" />}
        </span>
        <span className="fx-mid">
          {scored
            ? <b className="fx-score">{f.home_goals}-{f.away_goals}</b>
            : <span className="fx-vs">{kickoff || 'vs'}</span>}
          <span className={`fx-status fx-status-${f.status}`}>
            {f.status === 'live' && f.elapsed ? `${f.status_text} ${f.elapsed}'` : f.status_text}
          </span>
        </span>
        <span className="fx-team fx-away">
          {f.away_logo && <img className="fx-flag" src={f.away_logo} alt="" loading="lazy" />}
          <span className="fx-name">{f.away}</span>
        </span>
      </div>
      {!!f.goals?.length && (
        <ul className="fx-goals">
          {f.goals.map((g, i) => (
            <li key={i} className={`fx-goal fx-goal-${g.team || 'na'}`}>
              <span className="fx-goal-min">{g.minute}&apos;</span>
              <span className="fx-goal-icon">⚽</span>
              <span className="fx-goal-player">{g.player || '球员'}</span>
              {g.detail && g.detail !== '进球' && <em className="fx-goal-tag">{g.detail}</em>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function SportsScoresCardView({ card }: { card: SportsScoresCard }) {
  return (
    <div className="card card-evidence card-sports">
      <CardHead icon="⚽" title={card.title} freshness={card.freshness} />
      {card.fixtures.length === 0
        ? <div className="ev-empty">暂无比赛安排</div>
        : <div className="ev-fixtures">
            {card.fixtures.map((f, i) => <FixtureRow key={i} f={f} />)}
          </div>}
      {card.source && <div className="ev-card-foot">数据来源 {card.source}</div>}
    </div>
  )
}

function SportsScorersCardView({ card }: { card: SportsScorersCard }) {
  return (
    <div className="card card-evidence card-sports">
      <div className="ev-head">
        <span className="ev-head-title">👟 {card.title}</span>
        {card.season && <span className="ev-fresh">{card.season}</span>}
      </div>
      {card.scorers.length === 0
        ? <div className="ev-empty">暂无射手榜数据</div>
        : <ol className="sc-list">
            {card.scorers.map((s, i) => (
              <li key={i} className="sc-row">
                <span className="sc-rank">{s.rank}</span>
                <span className="sc-player">{s.player}</span>
                <span className="sc-team">{s.team}</span>
                <span className="sc-goals">{s.goals}<em>球</em></span>
              </li>
            ))}
          </ol>}
      {card.source && <div className="ev-card-foot">数据来源 {card.source}</div>}
    </div>
  )
}

// ─── 路线规划卡：出发地 → 途经点 → 目的地（导航确认途经点后，复用充电时间线样式）───

function RoutePlanCardView({ card }: { card: RoutePlanCard }) {
  const dur = card.duration_min
    ? `${Math.floor(card.duration_min / 60) ? `${Math.floor(card.duration_min / 60)}小时` : ''}${card.duration_min % 60 ? `${card.duration_min % 60}分钟` : ''}`
    : ''
  return (
    <div className="card card-evidence card-charge-route">
      <div className="ev-head">
        <span className="ev-head-title">🧭 路线规划</span>
        {card.distance_km ? (
          <span className="ev-fresh">{card.distance_km}km{dur ? ` · ${dur}` : ''}</span>
        ) : null}
      </div>
      <ul className="cr-line">
        <li className="cr-node cr-start">
          <span className="cr-dot" />
          <span className="cr-text">{card.origin || '当前位置'}</span>
        </li>
        {card.waypoints.map((w, i) => (
          <li key={i} className="cr-node cr-stop">
            <span className="cr-dot" />
            <span className="cr-text">
              <b>📍 {w.name}</b>
              {w.address && <em className="cr-km">{w.address}</em>}
            </span>
          </li>
        ))}
        <li className="cr-node cr-end">
          <span className="cr-dot" />
          <span className="cr-text">{card.destination}</span>
        </li>
      </ul>
    </div>
  )
}

// ─── 充能路线卡：出发地 → 沿途途经充电点 → 目的地 ───

function ChargingRouteCardView({ card }: { card: ChargingRouteCard }) {
  const dur = card.duration_min
    ? `${Math.floor(card.duration_min / 60) ? `${Math.floor(card.duration_min / 60)}小时` : ''}${card.duration_min % 60 ? `${card.duration_min % 60}分钟` : ''}`
    : ''
  return (
    <div className="card card-evidence card-charge-route">
      <div className="ev-head">
        <span className="ev-head-title">🔋 充能路线</span>
        {card.distance_km ? (
          <span className="ev-fresh">{card.distance_km}km{dur ? ` · ${dur}` : ''}</span>
        ) : null}
      </div>
      <ul className="cr-line">
        <li className="cr-node cr-start">
          <span className="cr-dot" />
          <span className="cr-text">出发地{card.soc ? `（电量 ${card.soc}）` : ''}</span>
        </li>
        {card.stops.map((s, i) => (
          <li key={i} className="cr-node cr-stop">
            <span className="cr-dot" />
            <span className="cr-text">
              <b>⚡ {s.name}</b>
              {s.at_km != null && <em className="cr-km">约 {s.at_km} km 处补电</em>}
            </span>
          </li>
        ))}
        <li className="cr-node cr-end">
          <span className="cr-dot" />
          <span className="cr-text">{card.destination}</span>
        </li>
      </ul>
      {card.stops.length === 0 && (
        <div className="cr-direct">电量充足，全程无需途中补电</div>
      )}
    </div>
  )
}

// ─── 行程卡：结构化多日行程（按天列停靠点 + 段间充电），复用充电时间线样式 ───

const TRIP_STOP_ICON: Record<string, string> = {
  attraction: '📍', meal: '🍜', hotel: '🏨', charging: '⚡', custom: '📌',
}

function TripItineraryCardView({ card, onAction }:
  { card: TripItineraryCard; onAction?: (text: string) => void }) {
  return (
    <div className="card card-evidence card-charge-route card-trip">
      <div className="ev-head">
        <span className="ev-head-title">🧭 {card.destination} · {card.days}天行程</span>
        {card.status === 'confirmed' && <span className="ev-fresh">已确认</span>}
      </div>
      {(card.itinerary || []).map((day, di) => {
        const charges = (day.legs || []).flatMap((l) => l.charging_stops || [])
        return (
          <div key={di} className="trip-day">
            <div className="trip-day-head">
              第{day.day_index}天{day.theme ? ` · ${day.theme}` : ''}
            </div>
            <ul className="cr-line">
              {day.stops.map((s, i) => {
                // 已接地的停靠点可点导航：派发整句『导航去第N天的X』→ 编排器路由 trip.navigate
                const go = s.grounded && onAction
                  ? () => onAction(`导航去第${day.day_index}天的${s.name}`)
                  : undefined
                return (
                  <li key={i} className={`cr-node cr-stop${s.grounded ? '' : ' trip-ungrounded'}`}>
                    <span className="cr-dot" />
                    <span className="cr-text">
                      {go ? (
                        <b className="trip-stop-go" role="button" tabIndex={0} onClick={go}
                           onKeyDown={(e) => { if (e.key === 'Enter') go() }}>
                          {TRIP_STOP_ICON[s.type] || '📍'} {s.name}
                          <span className="trip-go-hint">› 导航</span>
                        </b>
                      ) : (
                        <b>{TRIP_STOP_ICON[s.type] || '📍'} {s.name}</b>
                      )}
                      {!s.grounded
                        ? <em className="cr-km">待确认地点</em>
                        : (s.poi?.address && <em className="cr-km">{s.poi.address}</em>)}
                    </span>
                  </li>
                )
              })}
            </ul>
            {charges.length > 0 && (
              <div className="trip-charge-hint">
                ⚡ 途中补电 {charges.length} 次：{charges.map((c) => c.name).join('、')}
              </div>
            )}
          </div>
        )
      })}
      <div className="trip-voice-hint">🎙 点停靠点或说『下一站』『导航去第N天的某地点』即可导航</div>
    </div>
  )
}

// ─── POI 列表卡片 ───

function PoiListCardView({ card }: { card: PoiListCard }) {
  // 编号展示（便于「第N个」）：充电目的地候选 dest_choice / 顺路停靠途经点候选 waypoint_choice
  const isChoice = card.purpose === 'dest_choice' || card.purpose === 'waypoint_choice'
  return (
    <div className="card card-poi">
      <div className="card-header">{isChoice ? (card.title || '请选择') : `附近${card.keyword || ''}`}</div>
      <div className="card-poi-list">
        {card.items.map((item, i) => (
          <div key={i} className="poi-item">
            <div className="poi-name">{isChoice && <span className="poi-idx">{i + 1}.</span>}{item.name}</div>
            <div className="poi-info">
              {(item.rating ?? 0) > 0 && <span className="poi-rating">★ {item.rating}</span>}
              {(item.distance_km ?? 0) > 0 && <span className="poi-dist">{item.distance_km}km</span>}
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
