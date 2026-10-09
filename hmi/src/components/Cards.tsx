// 信息类 UI 卡片组件：天气 / 股票 / 新闻 / 搜索 / POI。
// 视觉照 Figma Make A-3~A-5（inline 样式 + --au-* token，证据范式：卡只给来源/要点，不复读气泡结论）。
import { useEffect, useState, type CSSProperties } from 'react'
import type {
  UiCard, WeatherCard, ForecastCard, StockCard,
  NewsCard, SearchCard, SearchAnswerCard, NewsDigestCard,
  SearchResultCard, NewsBriefCard, ResearchReportCard, SportsScoresCard, SportsScorersCard,
  RoutePlanCard, ChargingRouteCard, TripItineraryCard, PoiListCard, PoiDetailCard,
  PlaceListCard, PlaceDetailCard, IntentChoiceCard,
  ReminderListCard, ReminderCard, SceneCard, SceneListCard, Provenance,
  CardButton, MerchantCheckoutCard, PaymentQrCard, McpOrderCard, McpResultCard,
  ManualCard, ManualImage,
} from '../types'
import { airQualityBadge, buildKlineGeometry, priceDirection } from '../cardMath.mjs'
import { weatherAlertStatus, weatherAlertSummary } from '../weatherCard.mjs'
import { manualImages } from '../manualCard.mjs'
import {
  merchantActionButtons,
  merchantImageUrl,
  normalizeMerchantOrder,
  paymentPresentation,
  placeMenuAction,
  specChipAction,
} from '../merchantUi.mjs'
import { AQISection } from './aurora'
import { Icon, type IconName } from './Icon'
import { CardHeader, MetricTile, CardEmpty, NumericText } from './CardParts'
import { cardTitle, drivingCardSummary, present } from '../cardPresentation.mjs'

// AI 出品角标（照 A-4「AI · X」）：小极光点 + 虹彩文字，标识 AI 生成内容（§5）。
function AIBadge({ label }: { label: string }) {
  return (
    <div className="au-ai-badge">
      <span aria-hidden />
      <span>{label}</span>
    </div>
  )
}

// 当前电量进度条（照 A-5 充电路线卡）：soc 形如 "62%" → 解析为百分比 + 渐变填充。
function SocBar({ soc, dest, note }: { soc: string; dest: string; note?: string }) {
  const pct = Math.max(0, Math.min(100, parseInt(soc, 10) || 0))
  const ok = pct > 50
  return (
    <div className="cr-soc">
      <div className="cr-soc-head"><span>当前电量{note ? ` · ${note}` : ''}</span><NumericText as="b" className="au-num" style={{ color: ok ? 'var(--au-primary)' : 'var(--au-warn)' }}>{pct}%</NumericText></div>
      <div className="cr-soc-track"><div className="cr-soc-fill" style={{ width: `${pct}%`, background: ok ? 'linear-gradient(to right,#46D6E0,#34D399)' : 'linear-gradient(to right,#F59E0B,#EF4444)' }} /></div>
      <div className="cr-soc-foot"><span>出发地</span><span>目的地 · {dest}</span></div>
    </div>
  )
}

// 卡内分节横线（照 A-3 HR）
const CardHR = () => <div className="cv-shared-1"  />

// 商户卡动作的唯一渲染出口。onAction 复用 App.send，因此这些按钮始终是
// `is_confirmation=false` 的普通自然语言输入；创建/取消的写确认只在全局
// ConfirmBubble 中产生。按钮高度 44px，兼顾泊车触控。
function MerchantActionRow({ buttons, onAction }: {
  buttons: CardButton[]
  onAction?: (text: string) => void
}) {
  if (!buttons.length) return null
  return (
    <div className="cv-merchant-action-row-1" >
      {buttons.map((button) => {
        const destructive = /取消/.test(button.label)
        return (
          <button
            key={`${button.label}:${button.send_text}`}
            type="button"
            onClick={() => onAction?.(button.send_text)}
            disabled={!onAction}
            className="cv-merchant-action-row-2" style={{ cursor: onAction ? 'pointer' : 'default', color: destructive ? 'var(--au-warn)' : 'var(--au-text)', background: destructive ? 'rgba(245,158,11,0.09)' : 'var(--au-fill)', border: destructive ? '1px solid rgba(245,158,11,0.28)' : '1px solid var(--au-line-2)', opacity: onAction ? 1 : 0.58 }}
          >
            {button.label}
          </button>
        )
      })}
    </div>
  )
}

// ─── A-4 信息卡共享原语（照 A-4 源）───
// 内联线性图标（lucide 风，避免第三方依赖）
function Ico({ d, size = 24, color = 'currentColor', style, className }: { d: string | string[]; size?: number; color?: string; sw?: number; style?: CSSProperties; className?: string }) {
  const name: IconName = d === IC_CHEVRON ? 'chevron-down' : d === IC_EXT ? 'external-link' : d === IC_ALERT ? 'warning' : d === IC_BOOK ? 'manual' : 'chevron-right'
  return <Icon name={name} size={Math.max(size, 24)} color={color} style={style} className={className} />
}
const IC_CHEVRON = 'm6 9 6 6 6-6'
const IC_EXT = ['M15 3h6v6', 'M10 14 21 3', 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6']
const IC_ALERT = ['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z', 'M12 9v4', 'M12 17h.01']
const IC_BOOK = ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z']
const IC_MAX = ['M15 3h6v6', 'M9 21H3v-6', 'M21 3l-7 7', 'M3 21l7-7']

// 置信度徽章（A-4 ConfBadge；§3-A 语义色，绝不虹彩）
const _CONF_TONE: Record<string, { c: string; bg: string; bd: string; label: string }> = {
  high: { c: 'var(--au-conf-high)', bg: "color-mix(in srgb, var(--au-primary) 11%, transparent)", bd: "color-mix(in srgb, var(--au-primary) 26%, transparent)", label: '高' },
  medium: { c: 'var(--au-conf-mid)', bg: "color-mix(in srgb, var(--au-warn) 11%, transparent)", bd: "color-mix(in srgb, var(--au-warn) 26%, transparent)", label: '中' },
  low: { c: 'var(--au-conf-low)', bg: "color-mix(in srgb, var(--au-conf-low) 11%, transparent)", bd: "color-mix(in srgb, var(--au-conf-low) 24%, transparent)", label: '未充分核实' },
}
function ConfPill({ level, small }: { level?: string; small?: boolean }) {
  if (!level) return null
  const t = _CONF_TONE[level] ?? _CONF_TONE.medium
  return (
    <span className="au-confidence">
      <span className="cv-conf-pill-1" style={{ width: small ? 5 : 6, height: small ? 5 : 6, background: t.c }} />
      <span>置信度 {t.label}</span>
    </span>
  )
}
// 从 url 取裸域名（去 www.），取不到回退 source
function domainOf(url?: string, fallback?: string): string {
  if (url) { try { return new URL(url).hostname.replace(/^www\./, '') } catch { /* 非法 url */ } }
  return fallback || ''
}

// ─── 天气图标映射 ───
const WEATHER_GLYPH: Array<[string, IconName]> = [['\u96ea','weather-snow'],['\u96fe','weather-fog'],['\u973e','weather-haze'],['\u5c18','weather-dust'],['\u96f7','weather-thunder-alert'],['\u96e8','weather-rain'],['\u6674','weather-sunny'],['\u4e91','weather-cloudy'],['\u9634','weather-cloudy']]
function weatherGlyph(text: string, size: number, color = 'var(--au-text-2)') { const name = WEATHER_GLYPH.find(([word])=>String(text).includes(word))?.[1] || 'weather-cloudy'; return <Icon name={name} size={size} color={color} /> }

// ─── 卡片渲染入口 ───

export function CardRenderer({ card, onAction, driving = false }: { card: UiCard; onAction?: (text: string) => void; driving?: boolean }) {
  const prov = (card as { _prov?: Provenance })._prov
  const mockChannel = 'channel' in card && card.channel === 'mock' && prov?.mode !== 'mock'
  if (driving) {
    const summary = drivingCardSummary(card)
    if (!summary) return null
    return <section className="au-card au-driving-card" data-card-type={card.type}>
      <CardHeader icon="info" title={summary.title} />
      {summary.main && <div className="au-driving-metric"><NumericText as="b" className="au-num">{summary.main}</NumericText><span className="au-unit">{summary.unit}</span></div>}
      {summary.fields.map((value, index) => <div className="au-driving-field" key={index}>{value}</div>)}
      {summary.button && <button className="au-driving-card-action" disabled={!onAction} onClick={() => onAction?.(summary.button!.text)}>{summary.button.label}</button>}
      <ProvBadge prov={prov} />
      {mockChannel && <span className="au-prov mock">模拟数据</span>}
      {'demo' in card && card.demo === true && <span className="au-prov mock">演示商户</span>}
    </section>
  }
  if (card.type === 'card_group') return <div className="au-card-group">{card.items.map((child, i) => i === 0
    ? <CardRenderer key={i} card={child} onAction={onAction} />
    : <details key={i}><summary>{cardTitle(child)}<ProvBadge prov={(child as { _prov?: Provenance })._prov} /></summary><CardRenderer card={child} onAction={onAction} /></details>)}<ProvBadge prov={prov} /></div>
  return <div className="au-card-host" data-card-type={card.type}>
    {['news_list','search_list','poi_list','place_list'].includes(card.type) && 'items' in card && Array.isArray(card.items) && card.items.length === 0
      ? <section className="au-card"><CardHeader icon="info" title={cardTitle(card)} /><CardEmpty>没查到相关结果，可以换个关键词再试试。</CardEmpty></section>
      : <CardContent card={card} onAction={onAction} />}
    <div className="au-card-provenance"><ProvBadge prov={prov} />{mockChannel && <span className="au-prov mock">模拟数据</span>}</div>
  </div>
}
function CardContent({card,onAction}: {card: UiCard;onAction?: (text:string)=>void}) {
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
    case 'route_plan': return <RoutePlanCardView card={card} onAction={onAction} />
    case 'charging_route': return <ChargingRouteCardView card={card} />
    case 'trip_itinerary': return <TripItineraryCardView card={card} onAction={onAction} />
    case 'poi_list': return <PoiListCardView card={card} />
    case 'poi_detail': return <PoiDetailCardView card={card} />
    case 'place_list': return <PlaceListCardView card={card} onAction={onAction} />
    case 'place_detail': return <PlaceDetailCardView card={card} onAction={onAction} />
    case 'reminder_list': return <ReminderListCardView card={card} />
    case 'reminder_card': return <ReminderCardView card={card} onAction={onAction} />
    case 'scene_card': return <SceneCardView card={card} onAction={onAction} />
    case 'scene_list': return <SceneListCardView card={card} onAction={onAction} />
    case 'intent_choice': return <IntentChoiceCardView card={card} onAction={onAction} />
    case 'vision_answer': return <VisionAnswerCardView card={card} />
    case 'manual': return <ManualCardView card={card} />
    case 'payment_qr': return <PaymentQrCardView card={card} onAction={onAction} />
    case 'payment_receipt': return <PaymentReceiptCardView card={card} />
    case 'parking_fee': return <ParkingFeeCardView card={card} />
    case 'mcp_order': return <McpOrderCardView card={card} onAction={onAction} />
    // 只读工具的结果走信息卡：卡片形态必须与本轮真实动作一致（QA I-022）
    case 'mcp_result': return card.readonly
      ? <McpInfoCardView card={card} />
      : <McpOrderCardView card={card} onAction={onAction} />
    case 'merchant_checkout': return <MerchantCheckoutCardView card={card} onAction={onAction} />
    case 'merchant_choices': return <MerchantCheckoutCardView card={card} onAction={onAction} />
    case 'merchant_order_preview': return <MerchantCheckoutCardView card={card} onAction={onAction} />
    default: return <section className="au-card"><CardHeader icon="info" title="这条结果暂时无法展示" /><CardEmpty>可以让我换一种方式说明。</CardEmpty></section>
  }
}

// ─── 天气卡片 ───

// 数据真实性徽章（`_prov`，conventions §9.3）：mock=醒目琥珀「模拟数据」、degraded/cached=灰标、
// real=不打扰小字角标（来源 · 取数时间）。治理 P1 试点：weather / place 族 / search_result。
function ProvBadge({ prov }: { prov?: Provenance }) {
  if (!prov) return null
  const pill = (bg: string, fg: string, text: string, title?: string) => (
    <span title={title} className="cv-prov-badge-1" style={{ background: bg, color: fg }}>{text}</span>
  )
  if (prov.mode === 'mock') return pill('rgba(245,158,11,0.16)', 'var(--au-warn)', '模拟数据', '演示用模拟数据，非真实来源')
  if (prov.mode === 'degraded') return pill('rgba(148,163,184,0.16)', 'var(--au-text-2)', prov.note ? `降级 · ${prov.note}` : '降级', '真实数据，但经降级路径取得')
  if (prov.mode === 'cached') return pill('rgba(148,163,184,0.16)', 'var(--au-text-2)', prov.note ? `缓存 · ${prov.note}` : '缓存')
  const t = relativeTime(prov.fetched_at)
  return (
    <span title="数据来源 · 取数时间" className="cv-prov-badge-2" >
      {prov.vendor}{t ? ` · ${t}` : ''}
    </span>
  )
}

// CA2-17：共享服务账号如实标注。标签由桥按 servers.yaml 的 `account: service` 打，前端不自己判断。
function AccountBadge({ label }: { label?: string }) {
  if (!label) return null
  return (
    <span title="车上所有用户共用这一个商户账号" className="cv-account-badge-1" >{label}</span>
  )
}

function ManualCardView({ card }: { card: ManualCard }) {
  const images = manualImages(card) as ManualImage[]
  const chunks = (card.chunks || []).slice(0, 2)
  const title = card.document?.title || '车型用户手册'
  const revision = card.document?.revision || card._prov?.data_time || ''
  return (
    <div className="card card-evidence cv-manual-card-view-1" >
      <div className="cv-manual-card-view-2" >
        <span className="cv-manual-card-view-3" >
          <Icon name="manual" size={24} color="var(--au-primary)" />
        </span>
        <div className="cv-manual-card-view-4" >
          <div className="cv-manual-card-view-5" >{title}</div>
          <div className="cv-manual-card-view-6" >
            {[card.document?.vehicle_model, revision && `版本 ${revision}`].filter(Boolean).join(' · ')}
          </div>
        </div>

      </div>
      {images.length > 0 && (
        <>
          <CardHR />
          <div className="cv-manual-card-view-7" style={{ gridTemplateColumns: images.length > 1 ? '1fr 1fr' : '1fr' }}>
            {images.map((image) => (
              <figure key={image.asset_id} className="cv-manual-card-view-8" >
                <div className="cv-manual-card-view-9" style={{ minHeight: image.role === 'warning_icon' ? 148 : 128 }}>
                  <img
                    src={image.data_uri}
                    alt={image.caption || '手册配图'}
                    loading="lazy"
                    className="cv-manual-card-view-10" style={{ width: image.role === 'warning_icon' ? 116 : '100%', height: image.role === 'warning_icon' ? 116 : 'auto' }}
                  />
                </div>
                <figcaption className="cv-manual-card-view-11" >
                  <span className="cv-manual-card-view-12" >{image.caption || '手册配图'}</span>
                  <span className="cv-manual-card-view-13" >PDF 第 {image.page_start} 页</span>
                </figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
      {chunks.length > 0 && (
        <>
          <CardHR />
          <div className="cv-manual-card-view-14" >
            {chunks.map((chunk, index) => (
              <div key={`${chunk.page_start || 0}:${index}`} className="cv-manual-card-view-15" >
                <div className="cv-manual-card-view-16" >
                  <span className="cv-manual-card-view-17" >
                    {chunk.section_path?.slice(-1)[0] || `引用 ${index + 1}`}
                  </span>
                  {chunk.page_start ? <span className="cv-manual-card-view-18" >PDF 第 {chunk.page_start} 页</span> : null}
                </div>
                <p className="cv-manual-card-view-19" >
                  {chunk.content}
                </p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function WeatherCardView({ card }: { card: WeatherCard }) {
  const alert = weatherAlertSummary(card.alerts)
  const focus = card.focus
  const metrics: Array<{icon: IconName;label: string;value: unknown;unit?: string}> = focus ? [
    {icon:'temperature',label:'温度区间',value:[focus.temp_low,focus.temp_high].filter(present).join('～'),unit:'°C'},
    {icon:'humidity',label:'湿度',value:focus.humidity,unit:'%'},
    {icon:'wind',label:focus.wind_dir || '风',value:focus.wind_scale,unit:'级'},
    {icon:'umbrella',label:'降水',value:focus.precip,unit:'mm'},
    {icon:'uv',label:'紫外线',value:focus.uv_index},
    {icon:'weather-cloudy',label:'夜间',value:focus.text_night},
  ] : [
    {icon:'temperature',label:'体感',value:card.feels_like,unit:'°C'},
    {icon:'humidity',label:'湿度',value:card.humidity,unit:'%'},
    {icon:'wind',label:card.wind_dir || '风',value:card.wind_scale,unit:'级'},
    {icon:'umbrella',label:'降水',value:card.precip,unit:'mm'},
    {icon:'visibility',label:'能见度',value:card.visibility,unit:'km'},
    {icon:'pressure',label:'气压',value:card.pressure,unit:'hPa'},
  ]
  return <section className="au-card au-weather-evidence">
    <CardHeader icon={WEATHER_GLYPH.find(([word]) => (focus?.text_day || card.text || '').includes(word))?.[1] || 'weather-cloudy'} title={[card.city,focus?.label || '实况'].filter(Boolean).join(' · ')}
      meta={card.update_time && card.update_time !== 'mock' ? relativeTime(card.update_time) : undefined} />
    {alert && <div className="au-card-warning"><Icon name="warning" size={24} color="var(--au-warn)" />
      <div><strong>{alert.headline}</strong>{alert.detail && <div>{alert.detail}</div>}</div></div>}
    {metrics.some(m=>present(m.value)) ? <div className="au-metric-grid">{metrics.map(m=><MetricTile key={m.label} {...m} />)}</div> : <CardEmpty>天气数据暂不可用</CardEmpty>}
    {card.air_quality && <AQISection aqi={card.air_quality.aqi} category={card.air_quality.category} />}
    {!!card.indices?.length && <div className="au-card-tags">{card.indices.filter(t=>present(t.level)).slice(0,4).map(t=>
      <span key={t.name}>{t.name} <strong>{t.level}</strong></span>)}</div>}
  </section>
}

// ─── 天气预报卡片 ───

function ForecastCardView({ card }: { card: ForecastCard }) {
  return (
    <div className="card card-forecast">
      <div className="card-header"><span className="au-card-heading-icon"><Icon name="weather-cloudy" size={24} state="active" /></span>{card.city} 未来{card.days.length}天</div>
      <div className="card-forecast-days">
        {card.days.map((d, i) => (
          <div key={i} className="forecast-day">
            <div className="forecast-date">{d.date.slice(5)}</div>
            <div className="forecast-icon cv-forecast-card-view-1" >{weatherGlyph(d.text_day, 22)}</div>
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
    ? [{ l: '今开', v: last.open }, { l: '最高', v: last.high }, { l: '最低', v: last.low }, { l: '昨收', v: prev?.close }]
    : []
  // 市场标签优先用后端权威 market（腾讯 00700 是港股非 A 股）；缺失时按代码保守分类，不再硬编码 A股主板
  const marketTag = card.market || (() => {
    const d = (card.symbol || '').replace(/[^0-9]/g, '')
    if (d.length === 5) return '港股'
    if (d.length === 6) return d[0] === '6' ? '上证·A股' : (d[0] === '8' || d[0] === '4') ? '北证·A股' : '深证·A股'
    return card.symbol && !d ? '美股' : ''
  })()
  const stats = [
    { l: '成交量', v: last?.volume ?? null },
  ]
  return (
    <div className="card cv-stock-card-view-1" >
      {/* 头部 */}
      <div className="cv-stock-card-view-2" >
        <div>
          <div className="cv-stock-card-view-3 au-card-inline-heading"><Icon name="trend" size={28} state="active" />{card.name}</div>
          <div className="cv-stock-card-view-4" >
            <NumericText as="span" className="au-num cv-stock-card-view-5" >{card.symbol}</NumericText>
            {marketTag && <span className="cv-stock-card-view-6" >· {marketTag}</span>}
          </div>
        </div>
        {card.market_time && card.market_time !== 'mock' && (
          <div className="cv-stock-card-view-7" >
            <span className="cv-stock-card-view-8" >{card.market_time}</span>
          </div>
        )}
      </div>
      <CardHR />
      {/* 价格 + OHLC */}
      <div className="cv-stock-card-view-9" >
        <div>
          <div className="cv-stock-card-view-10" >
            <NumericText as="span" className="au-num cv-stock-card-view-11" style={{ color: 'var(--au-text)' }}>{card.price}</NumericText>
            <NumericText as="span" className="au-num cv-stock-card-view-12" ></NumericText>
          </div>
          <div className="cv-stock-card-view-13" >
            <NumericText as="span" className="au-num cv-stock-card-view-14" style={{ background: dir === 'down' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)', border: `1px solid ${cc}`, color: cc }}>{card.change}</NumericText>
            <NumericText as="span" className="au-num cv-stock-card-view-15" style={{ color: cc }}>{card.change_pct}</NumericText>
          </div>
        </div>
        {ohlc.length > 0 && (
          <div className="cv-stock-card-view-16" >
            {ohlc.filter(s => present(s.v)).map((s) => (
              <div key={s.l} className="cv-stock-card-view-17" >
                <span className="cv-stock-card-view-18" >{s.l}</span>
                <NumericText as="span" className="au-num cv-stock-card-view-19" >{s.v}</NumericText>
              </div>
            ))}
          </div>
        )}
      </div>
      <CardHR />
      {/* K 线 */}
      {candles.length ? (
        <div className="cv-stock-card-view-20" >
          <div className="cv-stock-card-view-21" >
            <span className="cv-stock-card-view-22" >日K线 · {candles.length}日</span>
            <div className="cv-stock-card-view-23" >
              {[].map((t, i) => (
                <span key={t} className="cv-stock-card-view-24" style={{ color: i === 1 ? 'var(--au-primary)' : 'var(--au-text-3)', fontWeight: i === 1 ? 600 : 400 }}>{t}</span>
              ))}
            </div>
          </div>
          <KlineChart card={card} />
        </div>
      ) : (
        <div className="cv-stock-card-view-25" >K 线数据暂不可用</div>
      )}
      <CardHR />
      {/* 指标 4 列 */}
      <div className="cv-stock-card-view-26" >
        {stats.filter(s => present(s.v)).map((s, i) => (
          <div key={i} className="cv-stock-card-view-27" >
            <NumericText as="div" className="au-num cv-stock-card-view-28" style={{ color: s.v ? 'var(--au-text)' : 'var(--au-text-3)' }}>{s.v ?? '—'}</NumericText>
            <div className="cv-stock-card-view-29" >{s.l}</div>
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
      <div className="card-header"><span className="au-card-heading-icon"><Icon name="newspaper" size={24} state="active" /></span>
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
      <div className="card-header cv-news-digest-card-view-1" ><Icon name="newspaper" size={24} color="var(--au-text)" />{card.topic || '今日热点'}</div>
      <AIBadge label="AI 摘要" />
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
      <div className="card-header"><span className="au-card-heading-icon"><Icon name="search" size={24} state="active" /></span>搜索「{card.query}」</div>
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
      <div className="card-header cv-search-answer-card-view-1" ><Icon name="search" size={24} color="var(--au-text)" />{card.query}</div>
      <AIBadge label="AI 回答" />
      <div className="search-answer-text">{card.answer}</div>
      {card.sources.length > 0 && (
        <div className="search-answer-sources">
          <button className="sources-toggle" onClick={() => setExpanded(!expanded)}>
            <Icon name="chevron-right" size={24} /> {card.sources.length} 条来源
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
  if (Number.isNaN(t)) return /^\d{1,2}:\d{2}/.test(iso) ? iso : ''
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

function ConfidenceBadge({ level }: { level?: string }) {
  return <ConfPill level={level} />
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
  const [open, setOpen] = useState(false)
  const sources = card.sources || []
  const shown = open ? sources : sources.slice(0, 3)
  const extra = sources.length - 3
  const fresh = relativeTime(card.freshness)
  return (
    <div className="card cv-search-result-card-view-1" >
      <div className="cv-search-result-card-view-2" >
        <div className="cv-search-result-card-view-3" >


        </div>
        <div className="cv-search-result-card-view-4" >
          <div className="cv-search-result-card-view-5" >
            <div className="cv-search-result-card-view-6" >
              <Icon name="search" size={24} color="var(--au-text-2)" />
              <span className="cv-search-result-card-view-7" >{card.query}</span>
            </div>
            <span className="cv-search-result-card-view-8" >找到 {sources.length} 条来源{fresh ? ` · 更新于${fresh}` : ''}</span>
          </div>
          <ConfPill level={card.confidence} />
        </div>
      </div>
      <CardHR />
      {shown.map((s, i) => {
        const dom = domainOf(s.url)
        return (
          <div key={i}>
            <div className="cv-search-result-card-view-9" >
              <div className="cv-search-result-card-view-10" >
                <NumericText as="span" className="au-num cv-search-result-card-view-11" >{i + 1}</NumericText>
                <span className="cv-search-result-card-view-12"  />
              </div>
              <div className="cv-search-result-card-view-13" >
                <div className="cv-search-result-card-view-14" >
                  <span className="cv-search-result-card-view-15" >{s.source || dom || '来源'}</span>
                  {dom && <span className="cv-search-result-card-view-16" >{dom}</span>}
                  <span className="cv-search-result-card-view-17" >{relativeTime(s.published) || s.published || ''}</span>
                </div>
                <p className="cv-search-result-card-view-18" >{s.title}</p>
              </div>
              {s.url && <a href={s.url} target="_blank" rel="noopener noreferrer" className="cv-search-result-card-view-19" ><Ico d={IC_EXT} size={24} color="var(--au-text-3)" /></a>}
            </div>
            {i < shown.length - 1 && <div className="cv-search-result-card-view-20"  />}
          </div>
        )
      })}
      {extra > 0 && (
        <div className="cv-search-result-card-view-21" >
          <button className="ev-more" onClick={() => setOpen(!open)}>{open ? '收起' : `更多 ${extra} 条 ›`}</button>
        </div>
      )}
    </div>
  )
}

// 深度调研报告卡（旗舰，照 A-4.3 重建）：AI 角标 + 问句 + 一句结论 + 元信息(置信/时效/引用)，
// 分节手风琴(编号方徽章+置信徽章+折叠体，首节默认展开) + 「展开完整报告」 + 未覆盖缺口(琥珀) + 全局参考来源。
// 行车听气泡简报、泊车展开读报告。
function ResearchSection({ idx, heading, body, citations, confidence, open, onToggle }: {
  idx: number; heading: string; body: string; citations?: number[]; confidence?: string; open: boolean; onToggle: () => void
}) {
  const t = _CONF_TONE[confidence ?? ''] ?? _CONF_TONE.low
  return (
    <div>
      <button onClick={onToggle} className="cv-research-section-1" >
        <span className="cv-research-section-2" style={{ background: open ? t.bg : 'var(--au-fill)', border: `1px solid ${open ? t.bd : 'var(--au-line-2)'}` }}>
          <NumericText as="span" className="au-num cv-research-section-3" style={{ color: open ? t.c : 'var(--au-text-3)' }}>{String(idx).padStart(2, '0')}</NumericText>
        </span>
        <span className="cv-research-section-4" style={{ color: open ? 'var(--au-text)' : 'var(--au-text-2)' }}>{heading}</span>
        <ConfPill level={confidence} small />
        <Ico d={IC_CHEVRON} size={24} color="var(--au-text-3)" className="cv-research-section-5" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
      </button>
      {open && (
        <div className="cv-research-section-6" >
          <p className="cv-research-section-7" >
            {body}
            {!!citations?.length && citations.map((c) => (
              <sup key={c} className="au-num cv-research-section-8" >[{c}]</sup>
            ))}
          </p>
        </div>
      )}
    </div>
  )
}

function ResearchReportCardView({ card }: { card: ResearchReportCard }) {
  const sections = card.sections || []
  const [openSet, setOpenSet] = useState<Set<number>>(new Set([0]))
  const allOpen = sections.length > 0 && openSet.size === sections.length
  const toggle = (i: number) => setOpenSet((prev) => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })
  const fresh = relativeTime(card.freshness)
  const sources = card.sources || []
  const gaps = card.gaps || []
  return (
    <div className="card card-research cv-research-report-card-view-1" >
      <div className="cv-research-report-card-view-2" >
        <AIBadge label="AI · 深度调研" />
        <div className="cv-research-report-card-view-3" >
          <Icon name="research" size={28} state="active" className="cv-research-report-card-view-4" />
          <span className="cv-research-report-card-view-5" >{card.question || '深度调研'}</span>
        </div>
        {card.summary && (
          <div className="cv-research-report-card-view-6" >
            <p className="cv-research-report-card-view-7" >{card.summary}</p>
          </div>
        )}
        <div className="cv-research-report-card-view-8" >
          <ConfPill level={card.overall_confidence} />
          {fresh && <><span className="cv-research-report-card-view-9" >·</span><span className="cv-research-report-card-view-10" >时效 {fresh}</span></>}
          {sources.length > 0 && <><span className="cv-research-report-card-view-11" >·</span><span className="cv-research-report-card-view-12" >引用 {sources.length} 篇</span></>}
        </div>
      </div>
      <CardHR />
      {sections.map((sec, i) => (
        <div key={i}>
          <ResearchSection idx={i + 1} heading={sec.heading} body={sec.body} citations={sec.citations} confidence={sec.confidence} open={openSet.has(i)} onToggle={() => toggle(i)} />
          {i < sections.length - 1 && <CardHR />}
        </div>
      ))}
      {sections.length > 1 && !allOpen && (
        <>
          <CardHR />
          <div className="cv-research-report-card-view-13" >
            <button onClick={() => setOpenSet(new Set(sections.map((_, i) => i)))} className="cv-research-report-card-view-14" >
              展开完整报告（共 {sections.length} 节）
            </button>
          </div>
        </>
      )}
      {gaps.length > 0 && (
        <>
          <CardHR />
          <div className="cv-research-report-card-view-15" >
            <div className="cv-research-report-card-view-16" >
              <Ico d={IC_ALERT} size={24} color="var(--au-warn)" />
              <span className="cv-research-report-card-view-17" >未覆盖数据缺口</span>
            </div>
            {gaps.map((g, i) => (
              <div key={i} className="cv-research-report-card-view-18" style={{ marginBottom: i < gaps.length - 1 ? 7 : 0 }}>
                <span className="cv-research-report-card-view-19"  />
                <span className="cv-research-report-card-view-20" >{g}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {sources.length > 0 && (
        <>
          <CardHR />
          <div className="cv-research-report-card-view-21" >
            <div className="cv-research-report-card-view-22" >参考来源</div>
            {sources.map((r, i) => (
              <div key={i} className="cv-research-report-card-view-23" >
                <sup className="au-num cv-research-report-card-view-24" >[{r.idx ?? i + 1}]</sup>
                <div>
                  {r.url
                    ? <a href={r.url} target="_blank" rel="noopener noreferrer" className="cv-research-report-card-view-25" >{r.title}</a>
                    : <span className="cv-research-report-card-view-26" >{r.title}</span>}
                  <span className="cv-research-report-card-view-27" > — {[r.source, r.published].filter(Boolean).join(' · ') || domainOf(r.url)}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// 新闻速览卡（照 A-4.2 重建）：AI 角标 + 「今日要闻 · 已摘要 N 条」+ 编号(02)+标题+摘要+来源·时间，
// 折叠「参考来源 N 个」展开来源点列；默认 5 条，「更多 N 条」展开。
function NewsBriefCardView({ card }: { card: NewsBriefCard }) {
  const [open, setOpen] = useState(false)
  const [showSrc, setShowSrc] = useState(false)
  const items = card.items || []
  const SHOW = 5
  const shown = open ? items : items.slice(0, SHOW)
  const extra = items.length - SHOW
  const srcCount = new Set(items.map((n) => n.source).filter(Boolean)).size
  return (
    <div className="card cv-news-brief-card-view-1" >
      <div className="cv-news-brief-card-view-2" >
        <AIBadge label="AI 摘要" />
        <div className="cv-news-brief-card-view-3" >
          <div className="cv-news-brief-card-view-4" >
            <Icon name="newspaper" size={24} color="var(--au-text-2)" />
            <span className="cv-news-brief-card-view-5" >{card.topic || '今日要闻'}</span>
          </div>
          <span className="cv-news-brief-card-view-6" >已摘要 {items.length} 条</span>
        </div>
      </div>
      <CardHR />
      {shown.map((n, i) => {
        const rel = relativeTime(n.publish_time)
        return (
          <div key={i}>
            <div className="cv-news-brief-card-view-7" >
              <NumericText as="span" className="au-num cv-news-brief-card-view-8" >{String(i + 1).padStart(2, '0')}</NumericText>
              <div className="cv-news-brief-card-view-9" >
                {n.url
                  ? <a href={n.url} target="_blank" rel="noopener noreferrer" className="cv-news-brief-card-view-10" >{n.title}</a>
                  : <div className="cv-news-brief-card-view-11" >{n.title}</div>}
                {n.summary && <p className="cv-news-brief-card-view-12" >{n.summary}</p>}
                {(n.source || rel) && (
                  <div className="cv-news-brief-card-view-13" style={{ marginTop: n.summary ? 0 : 5 }}>
                    {n.source && <span className="cv-news-brief-card-view-14" >{n.source}</span>}
                    {rel && <><span className="cv-news-brief-card-view-15" >·</span><span className="cv-news-brief-card-view-16" >{rel}</span></>}
                  </div>
                )}
              </div>
            </div>
            {i < shown.length - 1 && <div className="cv-news-brief-card-view-17"  />}
          </div>
        )
      })}
      <div className="cv-news-brief-card-view-18" >
        <div className="cv-news-brief-card-view-19" >
          {srcCount > 0 ? (
            <button onClick={() => setShowSrc(!showSrc)} className="cv-news-brief-card-view-20" >
              参考来源 {srcCount} 个
              <Ico d={IC_CHEVRON} size={24} color="var(--au-text-3)" className="cv-news-brief-card-view-21" style={{ transform: showSrc ? 'rotate(180deg)' : 'none' }} />
            </button>
          ) : <span />}
          {extra > 0 && <button className="ev-more" onClick={() => setOpen(!open)}>{open ? '收起' : `更多 ${extra} 条 ›`}</button>}
        </div>
        {showSrc && (
          <div className="cv-news-brief-card-view-22" >
            {items.map((n, i) => {
              const rel = relativeTime(n.publish_time)
              return (
                <div key={i} className="cv-news-brief-card-view-23" >
                  <span className="cv-news-brief-card-view-24"  />
                  <span className="cv-news-brief-card-view-25" >{n.source}</span>
                  {rel && <span className="cv-news-brief-card-view-26" >· {rel}</span>}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function TeamSquare({ name, color, flag }: { name: string; color: string; flag?: string }) {
  return (
    <div className="cv-team-square-1" >
      <div className="cv-team-square-2" style={{ background: `${color}22`, border: `2px solid ${color}55` }}>
        {/* 国家队显示国旗 emoji（.au-flag 套自托管国旗字体，Windows Chromium 也正常渲染）；无旗回落队名缩写 */}
        {flag
          ? <span className="au-flag cv-team-square-3" >{flag}</span>
          : <span className="cv-team-square-4" style={{ color }}>{name.slice(0, 2)}</span>}
      </div>
      <span className="cv-team-square-5" >{name}</span>
    </div>
  )
}

// 计分板（照 A-5 SportsCard）：主客队色块 + 大比分(分色) + 进球时间线(90分钟轴 + 标点 + 事件)
const HOME_C = '#5B8CFF'
const AWAY_C = '#9A6BFF'
function FixtureBoard({ f }: { f: SportsScoresCard['fixtures'][number] }) {
  const scored = (f.status === 'live' || f.status === 'finished') && (f.home_goals !== '' || f.away_goals !== '')
  const kickoff = f.kickoff && f.kickoff.includes('T') ? f.kickoff.slice(11, 16) : ''
  const goals = f.goals || []
  return (
    <div className="cv-fixture-board-1" >
      {/* 计分板 */}
      <div className="cv-fixture-board-2" >
        <TeamSquare name={f.home} color={HOME_C} flag={f.home_flag} />
        <div className="cv-fixture-board-3" >
          {scored ? (
            <span className="cv-fixture-board-4" >
              <NumericText as="span" className="au-num cv-fixture-board-5" style={{ color: HOME_C }}>{f.home_goals}</NumericText>
              <NumericText as="span" className="au-num cv-fixture-board-6" >–</NumericText>
              <NumericText as="span" className="au-num cv-fixture-board-7" style={{ color: AWAY_C }}>{f.away_goals}</NumericText>
            </span>
          ) : <span className="cv-fixture-board-8" >{kickoff || 'VS'}</span>}
          <span className="cv-fixture-board-9" style={{ fontWeight: f.status === 'live' ? 700 : 400, color: f.status === 'live' ? 'var(--au-warn)' : 'var(--au-text-3)' }}>
            {f.status === 'live' && f.elapsed ? `${f.status_text} ${f.elapsed}'` : f.status_text}
          </span>
        </div>
        <TeamSquare name={f.away} color={AWAY_C} flag={f.away_flag} />
      </div>
      {/* 进球时间线 */}
      {goals.length > 0 && (
        <div className="cv-fixture-board-10" >
          <div className="cv-fixture-board-11" >进球时间线</div>
          {/* 90 分钟时间轴 + 进球标点 */}
          <div className="cv-fixture-board-12" >
            <div className="cv-fixture-board-13" style={{ background: `linear-gradient(to right,${HOME_C}40,${AWAY_C}30)` }} />
            {goals.map((g, i) => {
              const m = Math.min(parseInt(g.minute, 10) || 0, 90)
              const color = g.team === 'away' ? AWAY_C : HOME_C
              return <span key={i} className="cv-fixture-board-14" style={{ left: `${(m / 90) * 100}%`, top: -3, background: color, boxShadow: `0 0 8px ${color}80` }} />
            })}
          </div>
          {/* 进球事件 */}
          {goals.map((g, i) => (
            <div key={i} className="cv-fixture-board-15" >
              <NumericText as="span" className="au-num cv-fixture-board-16" style={{ color: g.team === 'away' ? AWAY_C : HOME_C }}>{g.minute}&apos;</NumericText>
              <Icon name="sports" size={24} color="var(--au-text-2)" />
              <span className="cv-fixture-board-17" >{g.player || '球员'}</span>
              {g.detail && g.detail !== '进球' && <span className="cv-fixture-board-18" >{g.detail}</span>}
              <span className="au-flag cv-fixture-board-19" >{g.team === 'away' ? `${f.away_flag ? f.away_flag + ' ' : ''}${f.away}` : g.team === 'home' ? `${f.home_flag ? f.home_flag + ' ' : ''}${f.home}` : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SportsScoresCardView({ card }: { card: SportsScoresCard }) {
  return (
    <div className="card cv-sports-scores-card-view-1" >
      <div className="cv-sports-scores-card-view-2" >

        <div className="cv-sports-scores-card-view-3" >
          <span className="cv-sports-scores-card-view-4" ><Icon name="sports" size={24} color="var(--au-text)" />{card.title}</span>
          {card.freshness && <span className="cv-sports-scores-card-view-5" >{relativeTime(card.freshness)}</span>}
        </div>
      </div>
      <CardHR />
      {card.fixtures.length === 0
        ? <div className="cv-sports-scores-card-view-6" >暂无比赛安排</div>
        : card.fixtures.map((f, i) => (
            <div key={i}>
              {i > 0 && <CardHR />}
              <FixtureBoard f={f} />
            </div>
          ))}
      {card.source && <div className="cv-sports-scores-card-view-7" >数据来源 {card.source}</div>}
    </div>
  )
}

function SportsScorersCardView({ card }: { card: SportsScorersCard }) {
  return (
    <div className="card card-evidence card-sports">

      <div className="ev-head">
        <span className="ev-head-title cv-sports-scorers-card-view-1" ><Icon name="sports" size={24} color="var(--au-text)" />{card.title}</span>
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

function RoutePlanCardView({ card, onAction }: { card: RoutePlanCard; onAction?: (text: string) => void }) {
  const dur = card.duration_min
    ? `${Math.floor(card.duration_min / 60) ? `${Math.floor(card.duration_min / 60)}小时` : ''}${card.duration_min % 60 ? `${card.duration_min % 60}分钟` : ''}`
    : ''
  return (
    <div className="card cv-route-plan-card-view-1" >
      <div className="cv-route-plan-card-view-2" >

        <div className="cv-route-plan-card-view-3" >
          <span className="cv-route-plan-card-view-4" style={{ opacity: card.cancelled ? 0.55 : 1, textDecoration: card.cancelled ? 'line-through' : 'none' }}><Icon name="route-map" size={24} color="var(--au-text)" />{card.cancelled ? '导航已结束' : card.estimate ? '距离估算' : '规划路线'}</span>
          {(card.distance_km || dur) && <NumericText as="span" className="au-num cv-route-plan-card-view-5" >{dur}{card.distance_km ? `${dur ? ' · ' : ''}${card.distance_km}km` : ''}</NumericText>}
        </div>
      </div>
      <CardHR />
      <div className="cv-route-plan-card-view-6" >
        {[
          { type: 'origin', icon: 'location' as IconName, label: card.origin || '当前位置', sub: '出发' },
          ...card.waypoints.map((w) => ({ type: 'stop', icon: 'pin' as IconName, label: w.name, sub: w.address || '途经点' })),
          { type: 'dest', icon: 'flag' as IconName, label: card.destination, sub: '目的地' },
        ].map((n, i, arr) => {
          const color = n.type === 'origin' ? 'var(--au-primary)' : n.type === 'dest' ? '#34D399' : '#F59E0B'
          return (
            <div key={i} className="cv-route-plan-card-view-7" >
              <div className="cv-route-plan-card-view-8" >
                <span className="cv-route-plan-card-view-9" style={{ background: `${n.type === 'stop' ? 'rgba(245,158,11,0.15)' : color}`, border: `2px solid ${color}` }}><Icon name={n.icon} size={24} color={n.type === 'stop' ? '#F59E0B' : '#06080F'} /></span>
                {i < arr.length - 1 && <span className="cv-route-plan-card-view-10"  />}
              </div>
              <div className="cv-route-plan-card-view-11" >
                <div className="cv-route-plan-card-view-12" >{n.label}</div>
                <div className="cv-route-plan-card-view-13" >{n.sub}</div>
              </div>
            </div>
          )
        })}
      </div>
      <div className="cv-route-plan-card-view-14" >
        {/* 按钮回发的必须是**可直接执行的自然语言**（I-031：「解释定位原理」那颗按钮回发后
            被判没听清）。estimate 卡上它才是真按钮——只算不导之后「那就导过去」是下一步；
            规划卡上导航已经发出去了，再点一次没有语义，保持原来的装饰态。 */}
        {card.cancelled
          ? <button onClick={() => onAction?.(`导航去${card.destination}`)} disabled={!onAction}
              className="cv-route-plan-card-view-15" style={{ cursor: onAction ? 'pointer' : 'default', opacity: onAction ? 1 : 0.58 }}>重新导航</button>
          : card.estimate
          ? <button onClick={() => onAction?.(`导航去${card.destination}`)} disabled={!onAction}
              className="cv-route-plan-card-view-16" style={{ cursor: onAction ? 'pointer' : 'default', opacity: onAction ? 1 : 0.58 }}>导航过去</button>
          : <button className="cv-route-plan-card-view-17" disabled={!onAction} onClick={() => onAction?.(`导航去${card.destination}`)}>开始导航</button>}
      </div>
    </div>
  )
}

// ─── 充能路线卡：出发地 → 沿途途经充电点 → 目的地 ───

function ChargingRouteCardView({ card }: { card: ChargingRouteCard }) {
  const dur = card.duration_min
    ? `${Math.floor(card.duration_min / 60) ? `${Math.floor(card.duration_min / 60)}小时` : ''}${card.duration_min % 60 ? `${card.duration_min % 60}分钟` : ''}`
    : ''
  return (
    <div className="card cv-charging-route-card-view-1" >
      <div className="cv-charging-route-card-view-2" >

        <div className="cv-charging-route-card-view-3" >
          <span className="cv-charging-route-card-view-4" ><Icon name="charging-station" size={24} color="var(--au-warn)" />充电路线规划</span>
          {card.distance_km ? <NumericText as="span" className="au-num cv-charging-route-card-view-5" >{card.distance_km}km{dur ? ` · ${dur}` : ''}</NumericText> : null}
        </div>
      </div>
      <CardHR />
      {card.soc
        ? <div className="cv-charging-route-card-view-6" ><SocBar soc={card.soc} dest={card.destination} note={card.soc_note} /></div>
        : card.soc_note
          ? <div className="cv-charging-route-card-view-7" >当前电量：{card.soc_note}</div>
          : null}
      <CardHR />
      {card.stops.length > 0 ? (
        <div className="cv-charging-route-card-view-8" >
          <div className="cv-charging-route-card-view-9" >
            <span className="cv-charging-route-card-view-10"  />
            <div>
              <div className="cv-charging-route-card-view-11" >出发地</div>
              {card.soc && <div className="cv-charging-route-card-view-12" >当前电量 {card.soc}</div>}
            </div>
          </div>
          {card.stops.map((s, i) => (
            <div key={i}>
              <div className="cv-charging-route-card-view-13" >
                <span className="cv-charging-route-card-view-14"  />
                {s.at_km != null && <NumericText as="span" className="au-num cv-charging-route-card-view-15" >约 {s.at_km}km 处</NumericText>}
              </div>
              <div className="cv-charging-route-card-view-16" >
                <span className="cv-charging-route-card-view-17" ><Icon name="charging-station" size={24} color="#F59E0B" /></span>
                <div className="cv-charging-route-card-view-18" >
                  <div className="cv-charging-route-card-view-19" >{s.name}</div>
                  {s.address && <div className="cv-charging-route-card-view-20" >{s.address}</div>}
                </div>
              </div>
            </div>
          ))}
          <div className="cv-charging-route-card-view-21" ><span className="cv-charging-route-card-view-22"  /></div>
          <div className="cv-charging-route-card-view-23" >
            <span className="cv-charging-route-card-view-24"  />
            <div className="cv-charging-route-card-view-25" >{card.destination}</div>
          </div>
        </div>
      ) : (
        <div className="cv-charging-route-card-view-26" >
          <span className="cv-charging-route-card-view-27" ><Icon name="check-circle" size={28} color="#34D399" /></span>
          <div>
            <div className="cv-charging-route-card-view-28" >全程无需补电</div>
            <div className="cv-charging-route-card-view-29" >当前电量足以完成全程</div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── 行程卡：结构化多日行程（按天列停靠点 + 段间充电），复用充电时间线样式 ───

const TRIP_STOP_ICON: Record<string, IconName> = {
  attraction: 'landmark', meal: 'dining', hotel: 'hotel', charging: 'charging-station', custom: 'pin',
}

const DAY_COLORS = ['#46D6E0', '#5B8CFF', '#9A6BFF', '#FF6BD6', '#34D399']

function TripItineraryCardView({ card, onAction }:
  { card: TripItineraryCard; onAction?: (text: string) => void }) {
  const days = card.itinerary || []
  const [open, setOpen] = useState<Set<number>>(() => new Set(days.map((d) => d.day_index)))
  const toggle = (d: number) => setOpen((prev) => {
    const s = new Set(prev)
    if (s.has(d)) s.delete(d)
    else s.add(d)
    return s
  })
  return (
    <div className="card cv-trip-itinerary-card-view-1" >
      <div className="cv-trip-itinerary-card-view-2" >

        <div className="cv-trip-itinerary-card-view-3" >
          <span className="cv-trip-itinerary-card-view-4" ><Icon name="calendar-trip" size={24} color="var(--au-text)" />{card.destination} · {card.days}日行程</span>
          <span className="cv-trip-itinerary-card-view-5" >{card.status === 'confirmed' ? '已确认' : card.theme ? `《${card.theme}》主题` : '自驾 · AI 规划'}</span>
        </div>
      </div>
      <CardHR />
      <div className="cv-trip-itinerary-card-view-6" >
        {days.map((day, di) => {
          const color = DAY_COLORS[di % DAY_COLORS.length]
          const charges = (day.legs || []).flatMap((l) => l.charging_stops || [])
          const isOpen = open.has(day.day_index)
          return (
            <div key={di}>
              {charges.length > 0 && (
                <div className="cv-trip-itinerary-card-view-7" >
                  <Icon name="charging-station" size={24} color="var(--au-warn)" />
                  <span className="cv-trip-itinerary-card-view-8" >途中补电 {charges.length} 次：{charges.map((c) => c.name).join('、')}</span>
                </div>
              )}
              <button onClick={() => toggle(day.day_index)} className="cv-trip-itinerary-card-view-9" >
                <span className="cv-trip-itinerary-card-view-10" style={{ background: `${color}20`, border: `1px solid ${color}40`, color }}>D{day.day_index}</span>
                <span className="cv-trip-itinerary-card-view-11" >{day.city ? `${day.city} · ` : ''}{day.theme || `第${day.day_index}天`}</span>
                {day.weather?.text && (
                  <span title={day.weather.text} className="cv-trip-itinerary-card-view-12" >
                    {weatherGlyph(day.weather.text, 14)}
                    {day.weather.temp_low && day.weather.temp_high ? `${day.weather.temp_low}-${day.weather.temp_high}℃` : day.weather.text}
                  </span>
                )}
                <span className="cv-trip-itinerary-card-view-13" >{day.stops.length}个点</span>
                <span className="cv-trip-itinerary-card-view-14" style={{ transform: isOpen ? 'rotate(90deg)' : 'none' }}>›</span>
              </button>
              <div className="cv-trip-itinerary-card-view-15" style={{ maxHeight: isOpen ? 600 : 0 }}>
                {day.stops.map((s, i) => {
                  // 已接地的停靠点可点导航：派发整句『导航去第N天的X』→ 编排器路由 trip.navigate
                  const go = s.grounded && onAction ? () => onAction(`导航去第${day.day_index}天的${s.name}`) : undefined
                  return (
                    <div key={i} className="cv-trip-itinerary-card-view-16" >
                      <Icon name={TRIP_STOP_ICON[s.type] || 'pin'} size={24} color="var(--au-text-2)" className="cv-trip-itinerary-card-view-17"  />
                      <div className="cv-trip-itinerary-card-view-18" >
                        <div className="cv-trip-itinerary-card-view-19" style={{ color: s.grounded ? 'var(--au-text)' : 'var(--au-text-2)' }}>{s.name}</div>
                        <div className="cv-trip-itinerary-card-view-20" >{s.grounded ? (s.poi?.address || '') : '待确认地点'}</div>
                      </div>
                      {go && (
                        <button onClick={go} className="cv-trip-itinerary-card-view-21" >导航</button>
                      )}
                    </div>
                  )
                })}
              </div>
              {di < days.length - 1 && <CardHR />}
            </div>
          )
        })}
      </div>
      <div className="cv-trip-itinerary-card-view-22" >
        <Icon name="voice-input" size={24} color="var(--au-text-3)" />
        <span className="cv-trip-itinerary-card-view-23" >说「<span className="cv-trip-itinerary-card-view-24" >下一站</span>」或「<span className="cv-trip-itinerary-card-view-25" >导航去第 2 天的XX</span>」</span>
      </div>
    </div>
  )
}

// ─── POI 列表卡片 ───

function PoiListCardView({ card }: { card: PoiListCard }) {
  const isChoice = card.purpose === 'dest_choice' || card.purpose === 'waypoint_choice'
  const title = isChoice ? (card.title || '请选择') : `附近${card.keyword || '地点'}`
  return (
    <div className="card cv-poi-list-card-view-1" >
      <div className="cv-poi-list-card-view-2" >

        <div className="cv-poi-list-card-view-3" >
          <span className="cv-poi-list-card-view-4" ><Icon name="location" size={24} color="var(--au-text)" />{title}</span>
          <span className="cv-poi-list-card-view-5" >已更新 · 共 {card.items.length} 个</span>
        </div>
      </div>
      <CardHR />
      {card.items.map((item, i) => (
        <div key={item.id || i}>
          <div className="cv-poi-list-card-view-6" >
            <span className="cv-poi-list-card-view-7" >{i + 1}</span>
            <div className="cv-poi-list-card-view-8" >
              <div className="cv-poi-list-card-view-9" >
                <span className="cv-poi-list-card-view-10" >{item.name}</span>
                {(item.distance_km ?? 0) > 0 && <NumericText as="span" className="au-num cv-poi-list-card-view-11" >{item.distance_km}km</NumericText>}
              </div>
              {(item.rating ?? 0) > 0 && <div className="cv-poi-list-card-view-12" ><Icon name="star-filled" size={20} color="var(--au-warn)" style={{display: 'inline-block', verticalAlign: '-2px'}} /> {item.rating}</div>}
              {item.address && <div className="cv-poi-list-card-view-13" >{item.address}</div>}
            </div>
          </div>
          {i < card.items.length - 1 && <div className="cv-poi-list-card-view-14"  />}
        </div>
      ))}
      <div className="cv-poi-list-card-view-15" >
        <Icon name="voice-input" size={24} color="var(--au-text-3)" />
        <span className="cv-poi-list-card-view-16" >说「<span className="cv-poi-list-card-view-17" >导航去第 2 个</span>」或「<span className="cv-poi-list-card-view-18" >最近的{card.keyword || '地点'}</span>」</span>
      </div>
    </div>
  )
}

// ─── POI 详情卡片 ───

function PoiDetailCardView({ card }: { card: PoiDetailCard }) {
  return (
    <div className="card card-poi-detail">
      <div className="poi-detail-name au-card-inline-heading"><Icon name="location" size={24} state="active" />{card.name}</div>
      {card.address && <div className="poi-detail-addr cv-poi-detail-card-view-1" ><Icon name="pin" size={24} color="var(--au-text-3)" />{card.address}</div>}
      <div className="poi-detail-row">
        {card.rating > 0 && <span><Icon name="star-filled" size={20} color="var(--au-warn)" style={{display: 'inline-block', verticalAlign: '-2px'}} /> {card.rating}</span>}
        {card.category && <span>{card.category}</span>}
      </div>
    </div>
  )
}

// ─── 周边发现列表卡（nearby.search）───
// ─── 智能提醒列表卡（reminder.list）：时间条着色 + 待办芯片区（D7 右舞台走 AgendaStage，这里是气泡内轻卡）───
function ReminderListCardView({ card }: { card: ReminderListCard }) {
  const color = (s: string) =>
    s === 'fired' ? '#F59E0B' : s === 'done' ? 'var(--au-text-3)' : 'var(--au-primary)'
  const total = card.items.length + (card.todos?.length || 0)
  return (
    <div className="au-glass cv-reminder-list-card-view-1" >
      <div className="cv-reminder-list-card-view-2" >
        <span className="cv-reminder-list-card-view-3" >{card.date_label || '我的提醒'}</span>
        <span className="cv-reminder-list-card-view-4" >{total} 条</span>
      </div>
      {card.items.map((it) => (
        <div key={it.id} className="cv-reminder-list-card-view-5" >
          <NumericText as="span" className="au-num cv-reminder-list-card-view-6" style={{ color: color(it.status) }}>{it.time_display}</NumericText>
          <span className="cv-reminder-list-card-view-7" style={{ textDecoration: it.status === 'done' ? 'line-through' : 'none', opacity: it.status === 'done' ? 0.55 : 1 }}>{it.title}</span>
          {it.recur_label && <span className="cv-reminder-list-card-view-8" >{it.recur_label}</span>}
          {it.status === 'fired' && <span className="cv-reminder-list-card-view-9" >到点</span>}
        </div>
      ))}
      {(card.todos?.length || 0) > 0 && (
        <div className="cv-reminder-list-card-view-10" >
          <span className="cv-reminder-list-card-view-11" >待办 · {card.todos!.length}</span>
          {card.todos!.map((t) => (
            <span key={t.id} className="au-glass cv-reminder-list-card-view-12" style={{ textDecoration: t.status === 'done' ? 'line-through' : 'none' }}>{t.title}</span>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 智能提醒单条卡：created=创建回读 / updated=改期确认（P1a snooze/update）/ fired=到点触达（琥珀脉冲 + 完成/稍后 send_text 按钮）───
function ReminderCardView({ card, onAction }: { card: ReminderCard; onAction?: (text: string) => void }) {
  const fired = card.context === 'fired'
  const offer = card.context === 'offer'   // G7 询问式：记忆抽到未来事件 → 问要不要提醒
  const it = card.item
  const accent = fired || offer ? '#F59E0B' : 'var(--au-primary)'
  const label = fired ? '提醒到点' : offer ? '要提醒你吗' :
    card.context === 'updated' ? '已改时间' : '已创建提醒'
  return (
    <div className="au-glass" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10,
      ...(fired ? { animation: 'au-proactive-pulse-amber 3s ease-in-out infinite', border: "1px solid color-mix(in srgb, var(--au-warn) 35%, transparent)" } : {}) }}>
      <div className="cv-reminder-card-view-1" >
        <span className="cv-reminder-card-view-2" style={{ background: accent, boxShadow: `0 0 8px ${accent}` }} />
        <span className="cv-reminder-card-view-3" >{label}</span>
        {it.recur_label && <span className="cv-reminder-card-view-4" >{it.recur_label}</span>}
        {it.time_display && <NumericText as="span" className="au-num cv-reminder-card-view-5" style={{ color: fired ? '#F59E0B' : 'var(--au-text-2)' }}>{it.time_display}</NumericText>}
      </div>
      <div className="cv-reminder-card-view-6" >{it.title}</div>
      {(fired || offer) && (card.actions?.length || 0) > 0 && (
        <div className="cv-reminder-card-view-7" >
          {card.actions!.map((a) => (
            <button key={a.label} onClick={() => onAction?.(a.send_text)} className="au-glass cv-reminder-card-view-8"
              >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 场景卡（scene-orchestrator）：confirm 回读 / created 已存 / activated 已开 / suggest 建议 ───
// 动作清单是这张卡的核心——用户在**保存前**就看见场景到底会做什么、哪几步要二次确认，
// 不给"确认时看到 3 个动作、执行时只发生 2 个"的空间（设计 §5.1③）。
function SceneCardView({ card, onAction }: { card: SceneCard; onAction?: (t: string) => void }) {
  const meta: Record<SceneCard['context'], { label: string; accent: string }> = {
    confirm: { label: '待确认', accent: 'var(--au-warn)' },
    created: { label: '已保存', accent: 'var(--au-primary)' },
    activated: { label: '已开启', accent: 'var(--au-primary)' },
    suggest: { label: 'AI 建议', accent: "var(--au-warn)" },
  }
  const { label, accent } = meta[card.context] || meta.created
  const steps = card.actions_preview || []
  return (
    <div className="au-glass cv-scene-card-view-1" >
      <div className="cv-scene-card-view-2" >
        <span className="au-card-heading-icon"><Icon name="lightbulb" size={24} color={accent} /></span>
        <span className="cv-scene-card-view-4" >{card.name}</span>
        {card.context === 'suggest' ? <AIBadge label="AI 建议" /> : <span className="cv-scene-card-view-5">{label}</span>}
      </div>
      {card.description && (
        <div className="cv-scene-card-view-6" >{card.description}</div>
      )}
      <div className="cv-scene-card-view-7" >
        {steps.map((s, i) => (
          <div key={i} className="cv-scene-card-view-8" >
            <NumericText as="span" className="au-num cv-scene-card-view-9" >{i + 1}</NumericText>
            <span className="cv-scene-card-view-10" >{s.label}</span>
            {s.danger && (
              <span className="cv-scene-card-view-11" >需确认</span>
            )}
          </div>
        ))}
      </div>
      {(card.buttons?.length || 0) > 0 && (
        <div className="cv-scene-card-view-12" >
          {card.buttons!.filter((b) => b.send_text).map((b) => (
            <button key={b.label} onClick={() => onAction?.(b.send_text)} className="au-glass cv-scene-card-view-13"
              >
              {b.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 场景列表卡（scene.list）：我建的 / 内置，条目可点 → 回发「开启X」 ───
function SceneListCardView({ card, onAction }: { card: SceneListCard; onAction?: (t: string) => void }) {
  const Group = ({ title, items }: { title: string; items: SceneListCard['mine'] }) => (
    <div className="cv-scene-list-card-view-1" >
      <span className="cv-scene-list-card-view-2" >{title} · {items.length}</span>
      {items.map((s) => (
        <button key={s.id} onClick={onAction ? () => onAction(`开启${s.name}`) : undefined}
          className="cv-scene-list-card-view-3" style={{ cursor: onAction ? 'pointer' : 'default' }}>
          <span className="cv-scene-list-card-view-4" >{s.name}</span>
          {s.description && (
            <span className="cv-scene-list-card-view-5" >{s.description}</span>
          )}
          {!!s.action_count && (
            <NumericText as="span" className="au-num cv-scene-list-card-view-6" >{s.action_count} 步</NumericText>
          )}
        </button>
      ))}
    </div>
  )
  return (
    <div className="au-glass cv-scene-list-card-view-7" >
      {card.mine?.length > 0 && <Group title="我建的" items={card.mine} />}
      {card.builtin?.length > 0 && <Group title="内置" items={card.builtin} />}
      <div className="cv-scene-list-card-view-8" >说「创建钓鱼模式：座椅放平、氛围灯调暗」就能造一个</div>
    </div>
  )
}

// ─── R4.4 路由歧义澄清卡：一句提问 + 2~3 消歧选项（点/说「第N个」→ 回发 send_text 作新指令）───
function IntentChoiceCardView({ card, onAction }: { card: IntentChoiceCard; onAction?: (t: string) => void }) {
  const options = (card.options || []).filter((o) => o?.send_text)
  return (
    <div className="card cv-intent-choice-card-view-1" >
      <div className="cv-intent-choice-card-view-2" >

        <div className="cv-intent-choice-card-view-3" >
          <Icon name="info" size={24} color="var(--au-primary)" className="cv-intent-choice-card-view-4"  />
          <span className="cv-intent-choice-card-view-5" >{card.question}</span>
        </div>
      </div>
      <CardHR />
      <div className="cv-intent-choice-card-view-6" >
        {options.map((o, i) => (
          <button
            key={i}
            onClick={onAction ? () => onAction(o.send_text) : undefined}
            className="cv-intent-choice-card-view-7" style={{ cursor: onAction ? 'pointer' : 'default' }}
          >
            <span className="cv-intent-choice-card-view-8" >{i + 1}</span>
            <span className="cv-intent-choice-card-view-9" >{o.label}</span>
          </button>
        ))}
      </div>
      <div className="cv-intent-choice-card-view-10" >说「第一个/第二个」或点选即可</div>
    </div>
  )
}

function PlaceListCardView({ card, onAction }: { card: PlaceListCard; onAction?: (t: string) => void }) {
  const title = `附近${card.keyword || card.category || '地点'}`
  return (
    <div className="card cv-place-list-card-view-1" >
      <div className="cv-place-list-card-view-2" >
        <div className="cv-place-list-card-view-3" >


        </div>
        <div className="cv-place-list-card-view-4" >
          <span className="cv-place-list-card-view-5" ><Icon name="location" size={24} color="var(--au-text)" />{title}</span>
          <span className="cv-place-list-card-view-6" >共 {card.items.length} 家</span>
        </div>
      </div>
      <CardHR />
      {card.items.map((item, i) => (
        <div key={item.id || i}>
          <div
            onClick={onAction ? () => onAction(`看${item.name}的详情`) : undefined}
            className="cv-place-list-card-view-7" style={{ cursor: onAction ? 'pointer' : 'default' }}
          >
            <span className="cv-place-list-card-view-8" >{i + 1}</span>
            <div className="cv-place-list-card-view-9" >
              <div className="cv-place-list-card-view-10" >
                <span className="cv-place-list-card-view-11" >{item.name}</span>
                <span className="cv-place-list-card-view-12" >
                  {(() => {
                    // 品牌门店（瑞幸/麦当劳）补「看菜单」直达：发现→看单→点单全程可点按
                    const menu = placeMenuAction(item.name)
                    return menu && onAction ? (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); onAction(menu.send_text) }}
                        className="cv-place-list-card-view-13"
                      >{menu.label}</button>
                    ) : null
                  })()}
                  {(item.distance_km ?? 0) > 0 && <NumericText as="span" className="au-num cv-place-list-card-view-14" >{item.distance_km}km</NumericText>}
                </span>
              </div>
              <div className="cv-place-list-card-view-15" style={{ marginBottom: item.address ? 4 : 0 }}>
                {(item.rating ?? 0) > 0 && <span className="cv-place-list-card-view-16" ><Icon name="star-filled" size={20} color="var(--au-warn)" style={{display: 'inline-block', verticalAlign: '-2px'}} /> {item.rating}</span>}
                {item.cost && <span className="cv-place-list-card-view-17" >人均 ¥{item.cost}</span>}
                {item.open_today && <span className="cv-place-list-card-view-18" >{item.open_today}</span>}
              </div>
              {item.address && <div className="cv-place-list-card-view-19" >{item.address}</div>}
              {item.tags && <div className="cv-place-list-card-view-20" >{item.tags.split(/[,，]/).slice(0, 3).join(' · ')}</div>}
            </div>
          </div>
          {i < card.items.length - 1 && <div className="cv-place-list-card-view-21"  />}
        </div>
      ))}
      <div className="cv-place-list-card-view-22" >
        <Icon name="voice-input" size={24} color="var(--au-text-3)" />
        <span className="cv-place-list-card-view-23" >说「<span className="cv-place-list-card-view-24" >看第 1 个详情</span>」或「<span className="cv-place-list-card-view-25" >导航去第 2 个</span>」</span>
      </div>
    </div>
  )
}

// ─── 周边发现详情卡（nearby.detail）───
function PlaceDetailRow({ icon, label, text }: { icon?: IconName; label?: string; text: string }) {
  return (
    <div className="cv-place-detail-row-1" >
      {icon && <Icon name={icon} size={24} color="var(--au-text-3)" />}
      {label && <span className="cv-place-detail-row-2" >{label}</span>}
      <span className="cv-place-detail-row-3" >{text}</span>
    </div>
  )
}

function PlaceDetailCardView({ card, onAction }: { card: PlaceDetailCard; onAction?: (t: string) => void }) {
  const hours = card.open_today || card.open_week
  const tel = (card.tel || '').split(/[;；/]/)[0].trim()
  return (
    <div className="card cv-place-detail-card-view-1" >
      <div className="cv-place-detail-card-view-2" >
        <div className="cv-place-detail-card-view-3" >


        </div>
        <div className="cv-place-detail-card-view-4 au-card-inline-heading"><Icon name="dining" size={28} state="active" />{card.name}</div>
        <div className="cv-place-detail-card-view-5" >
          {(card.rating ?? 0) > 0 && <span className="cv-place-detail-card-view-6" ><Icon name="star-filled" size={20} color="var(--au-warn)" style={{display: 'inline-block', verticalAlign: '-2px'}} /> {card.rating}</span>}
          {card.cost && <span className="cv-place-detail-card-view-7" >人均 ¥{card.cost}</span>}
          {card.category && <span className="cv-place-detail-card-view-8" >{card.category.split(/[;；]/)[0]}</span>}
        </div>
      </div>
      {card.photos && card.photos.length > 0 && (
        <div className="cv-place-detail-card-view-9" >
          {card.photos.slice(0, 4).map((u, i) => (
            <img key={i} src={u} alt="" loading="lazy"
              onError={(e) => { e.currentTarget.style.display = 'none' }}
              className="cv-place-detail-card-view-10"  />
          ))}
        </div>
      )}
      <CardHR />
      <div className="cv-place-detail-card-view-11" >
        {hours && <PlaceDetailRow icon="clock" text={hours} />}
        {tel && <PlaceDetailRow label="电话" text={card.tel!} />}
        {card.tags && <PlaceDetailRow label="特色" text={card.tags.split(/[,，]/).slice(0, 4).join(' · ')} />}
        {card.address && <PlaceDetailRow icon="pin" text={card.address} />}
      </div>
      <div className="cv-place-detail-card-view-12" >
        {onAction && (
          <button
            onClick={() => onAction(`导航去${card.name}`)}
            className="cv-place-detail-card-view-13"
          >
            <Icon name="compass" size={24} color="#fff" />导航
          </button>
        )}
        {tel && (
          <a href={`tel:${tel}`}
            className="cv-place-detail-card-view-14"
          >拨打电话</a>
        )}
      </div>
    </div>
  )
}

// ─── M4 P4 看一看卡 ───
// 极简：一行问题 + 答案 + **模拟摄像头角标**。角标不是装饰——PoC 的画面来自设备摄像头，
// 不说清楚就是拿演示当真实（铁律③）。
function VisionAnswerCardView({ card }: { card: any }) {
  return (
    <div className="au-card cv-vision-answer-card-view-1" >
      <CardHeader icon="camera" title="看一看" meta={<AIBadge label="AI 回答" />} />
      <div className="cv-vision-answer-card-view-2" >
        <span className="cv-vision-answer-card-view-3" >{card.question || '看一看'}</span>
        {card.simulated && (
          <span className="cv-vision-answer-card-view-4" >模拟车外摄像头</span>
        )}
      </div>
      <div className="cv-vision-answer-card-view-5" >{card.answer}</div>
    </div>
  )
}

// ─── 支付卡族（§9.17，2026-08-11 批 2）───

// 付款码卡：qr_svg 是网关生成的 data URI（前端零 QR 依赖）；本地倒计时到
// expires_at_ms 置灰——**不轮询网关**（HMI 与网关无通道），支付成功的回执由
// 统一主动引擎推 payment_receipt 卡。mock 渠道的 _prov 角标由 ProvBadge 渲染。
function PaymentQrCardView({ card, onAction }: { card: PaymentQrCard; onAction?: (text: string) => void }) {
  const [now, setNow] = useState(() => Date.now())
  const [copied, setCopied] = useState(false)
  const expiresAt = Number(card.expires_at_ms || 0)
  const expired = expiresAt > 0 && now >= expiresAt
  const presentation = paymentPresentation(card)
  const actionButtons = merchantActionButtons(card)
  useEffect(() => {
    if (!expiresAt || expired) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [expiresAt, expired])
  const remain = expiresAt > 0 ? Math.max(0, Math.floor((expiresAt - now) / 1000)) : 0
  const mm = String(Math.floor(remain / 60)).padStart(2, '0')
  const ss = String(remain % 60).padStart(2, '0')
  const copyLink = async () => {
    if (!presentation.safeUrl || expired) return
    try {
      await navigator.clipboard.writeText(presentation.safeUrl)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="au-card cv-payment-qr-card-view-1" >
      <div className="cv-payment-qr-card-view-2" >
        <span className="cv-payment-qr-card-view-3" >{presentation.title}</span>
        <span className="cv-payment-qr-card-view-4" >{card.amount}</span>
        <AccountBadge label={card.account_label} />
        <span className="cv-payment-qr-card-view-5"  />

      </div>
      {presentation.hasQr ? (
        <div className="cv-payment-qr-card-view-6" style={{ opacity: expired ? 0.35 : 1 }}>
          <img src={card.qr_svg} alt="付款码" className="cv-payment-qr-card-view-7"  />
          {expired && (
            <span className="cv-payment-qr-card-view-8" >已过期</span>
          )}
        </div>
      ) : presentation.safeUrl && !expired ? (
        <div className="cv-payment-qr-card-view-9" >
          <a
            href={presentation.safeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="cv-payment-qr-card-view-10"
          >打开安全支付链接</a>
          <button
            type="button"
            onClick={copyLink}
            className="cv-payment-qr-card-view-11" style={{ color: copied ? 'var(--au-online)' : 'var(--au-text-2)' }}
          >{copied ? '已复制' : '复制链接'}</button>
        </div>
      ) : null}
      <div className="cv-payment-qr-card-view-12" >
        {expired ? '支付入口已过期，请重新发起' :
          expiresAt > 0 ? `${presentation.hint} · ${mm}:${ss} 后过期` : presentation.hint}
      </div>
      {card.merchant_note && (
        <div className="cv-payment-qr-card-view-13" >
          {card.merchant_note}
        </div>
      )}
      <MerchantActionRow buttons={actionButtons} onAction={onAction} />
    </div>
  )
}

// 支付回执卡：worker 确认收款后经主动引擎推送；parking 直发的历史通道同渲。
function PaymentReceiptCardView({ card }: { card: any }) {
  return (
    <div className="au-card cv-payment-receipt-card-view-1" >
      <div className="cv-payment-receipt-card-view-2" >
        <span aria-hidden className="cv-payment-receipt-card-view-3" ><Icon name="check" size={24} color="var(--au-online)" /></span>
        <span className="cv-payment-receipt-card-view-4" >支付成功</span>
        {card.amount && <span className="cv-payment-receipt-card-view-5" >{card.amount}</span>}
        <span className="cv-payment-receipt-card-view-6"  />

      </div>
      <div className="cv-payment-receipt-card-view-7" >
        凭证号 {card.receipt_id}{card.order_id ? ` · 订单 ${card.order_id}` : ''}
      </div>
    </div>
  )
}

// MCP 桥订单/结果卡（§9.9 批 3 清偿的存量欠账）：demo_label 角标在此第一次有了
// 渲染出口——「演示商户」三重冗余的第二重此前只在后端数据里成立。
function McpOrderCardView({ card, onAction }: {
  card: McpOrderCard | McpResultCard
  onAction?: (text: string) => void
}) {
  const order = normalizeMerchantOrder(card)
  const actionButtons = merchantActionButtons(card)
  const sku = card.type === 'mcp_order'
    ? card.sku
    : typeof card.sku === 'string' ? card.sku : ''
  const size = card.type === 'mcp_order'
    ? card.size
    : typeof card.size === 'string' ? card.size : ''
  const duplicate = card.type === 'mcp_order'
    ? card.duplicate
    : card.duplicate === true
  return (
    <div className="au-card cv-mcp-order-card-view-1" >
      <div className="cv-mcp-order-card-view-2" >
        <span className="cv-mcp-order-card-view-3" >
          {order.brand}
        </span>
        <span className="cv-mcp-order-card-view-4" >
          {card.type === 'mcp_order' ? '商户订单' : '商户服务'}
        </span>
        {card.demo && (
          <span className="cv-mcp-order-card-view-5" >{card.demo_label || '演示商户'}</span>
        )}
        <AccountBadge label={card.account_label} />
        <span className="cv-mcp-order-card-view-6"  />

      </div>
      <div className="cv-mcp-order-card-view-7" >
        <div className="cv-mcp-order-card-view-8" >
          <span className="cv-mcp-order-card-view-9" >订单号</span>
          <span className="cv-mcp-order-card-view-10"  />
          {order.status && <span className="cv-mcp-order-card-view-11" >{order.status}</span>}
        </div>
        <NumericText as="div"
          className="au-num cv-mcp-order-card-view-12"
          data-order-id={order.orderId || undefined}
          title={order.orderId || undefined}
          style={{ overflowWrap: 'anywhere', wordBreak: 'break-all' }}
        >
          {order.orderId || '待商户回传'}
        </NumericText>
        {order.storeName && <div className="cv-mcp-order-card-view-13" >门店 · {order.storeName}</div>}
        {order.fulfillment && <div className="cv-mcp-order-card-view-14" >取餐 · {order.fulfillment}</div>}
        {order.items.map((item, i) => (
          <div key={`${item.name}:${i}`} className="cv-mcp-order-card-view-15" >
            <span className="cv-mcp-order-card-view-16" >{item.name}{item.specs ? <span className="cv-mcp-order-card-view-17" > · {item.specs}</span> : null}</span>
            <NumericText as="span" className="au-num cv-mcp-order-card-view-18" >×{item.quantity}</NumericText>
          </div>
        ))}
        {(sku || size) && !order.items.length && (
          <div className="cv-mcp-order-card-view-19" >
            {sku}{size ? ` · ${size}` : ''}
          </div>
        )}
      </div>
      {(order.amount || duplicate) && (
        <div className="cv-mcp-order-card-view-20" >
          {order.amount && <><span className="cv-mcp-order-card-view-21" >应付</span><span className="cv-mcp-order-card-view-22" >{order.amount}</span></>}
          <span className="cv-mcp-order-card-view-23"  />
          {duplicate && <span className="cv-mcp-order-card-view-24" >已有订单 · 幂等命中</span>}
        </div>
      )}
      <MerchantActionRow buttons={actionButtons} onAction={onAction} />
    </div>
  )
}

// 只读商户工具的结果卡（QA I-022）。**刻意极简**：内容由 speech 承载（这些工具都是
// speech_mode: summarize），卡片只留三件后端已经保证、话术里说不清的事——
// 品牌、演示商户角标、数据真实性徽章。订单号/状态/应付/操作按钮一个都不出现：
// 那些字段在一次营养成分查询里没有对应物，渲染出来就是无中生有。
function McpInfoCardView({ card }: { card: McpResultCard }) {
  const order = normalizeMerchantOrder(card)
  return (
    <div className="au-card cv-mcp-info-card-view-1" >
      <div className="cv-mcp-info-card-view-2" >
        <span className="cv-mcp-info-card-view-3" >
          {order.brand}
        </span>
        <span className="cv-mcp-info-card-view-4" >商户信息</span>
        {card.demo && (
          <span className="cv-mcp-info-card-view-5" >{card.demo_label || '演示商户'}</span>
        )}
        <AccountBadge label={card.account_label} />
        <span className="cv-mcp-info-card-view-6"  />

      </div>
      {typeof card.tool === 'string' && card.tool && (
        <div className="cv-mcp-info-card-view-7" >来源 · {card.tool}</div>
      )}
    </div>
  )
}

function centsLabel(value: unknown): string {
  const cents = Number(value)
  if (!Number.isInteger(cents) || cents < 0) return ''
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}元`
}

function MerchantCheckoutCardView({ card, onAction }: {
  card: MerchantCheckoutCard
  onAction?: (text: string) => void
}) {
  const order = normalizeMerchantOrder(card)
  const isChoices = card.type === 'merchant_choices' || card.stage === 'choices'
  const title = card.title || (isChoices
    ? `选择${order.brand}${card.choice_kind === 'store' ? '门店' : '商品'}`
    : card.stage === 'cancel' ? `${order.brand}取消订单` : `${order.brand}订单预览`)
  const options = Array.isArray(card.options) && card.options.length
    ? card.options
    : isChoices && Array.isArray(card.items) ? card.items : []
  const optionButtons = Array.isArray(card.buttons) && card.buttons.length
    ? merchantActionButtons({ buttons: card.buttons })
    : merchantActionButtons({ options: card.options || [] })
  const regularButtons = merchantActionButtons({ ...card, options: [] })
  const discount = typeof card.discount === 'string' && card.discount.trim()
    ? card.discount.trim()
    : centsLabel(card.discount_cents)

  return (
    <div className="au-card cv-merchant-checkout-card-view-1" >
      <div className="cv-merchant-checkout-card-view-2" >
        <span className="cv-merchant-checkout-card-view-3"  />
        <span className="cv-merchant-checkout-card-view-4" >{title}</span>
        <AccountBadge label={card.account_label} />
        <span className="cv-merchant-checkout-card-view-5"  />

      </div>

      {isChoices ? (
        <div className="cv-merchant-checkout-card-view-6" >
          {typeof card.total === 'number' && card.total > optionButtons.length && (
            <div className="cv-merchant-checkout-card-view-7" >
              在售共 {card.total} 款，这里是一页——按分类看或直接报名字
            </div>
          )}
          {Array.isArray(card.categories) && card.categories.length > 0 && (
            <div className="cv-merchant-checkout-card-view-8" >
              {card.categories.map((cat) => (cat?.label && cat?.send_text ? (
                <button
                  key={cat.label}
                  type="button"
                  disabled={!onAction}
                  onClick={onAction ? () => onAction(cat.send_text!) : undefined}
                  className="cv-merchant-checkout-card-view-9" style={{ cursor: onAction ? 'pointer' : 'default' }}
                >{cat.label}</button>
              ) : null))}
            </div>
          )}
          {optionButtons.map((button, index) => {
            const option = options[index]
            const image = merchantImageUrl((option as { image_url?: string })?.image_url)
            return (
              <button
                key={`${button.label}:${button.send_text}`}
                type="button"
                onClick={() => onAction?.(button.send_text)}
                disabled={!onAction}
                className="cv-merchant-checkout-card-view-10" style={{ cursor: onAction ? 'pointer' : 'default' }}
              >
                {image && (
                  // 图加载失败就把自己摘掉——车机上一张裂图比没有图更糟
                  <img
                    src={image}
                    alt=""
                    loading="lazy"
                    referrerPolicy="no-referrer"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                    className="cv-merchant-checkout-card-view-11"
                  />
                )}
                <span className="cv-merchant-checkout-card-view-12" >
                  <span className="cv-merchant-checkout-card-view-13" >{button.label}</span>
                  {option?.subtitle && <span className="cv-merchant-checkout-card-view-14" >{option.subtitle}</span>}
                </span>
              </button>
            )
          })}
        </div>
      ) : (
        <>
          <div className="cv-merchant-checkout-card-view-15" >
            {order.storeName && <div className="cv-merchant-checkout-card-view-16" >门店 · {order.storeName}</div>}
            {order.items.map((item, i) => (
              <div key={`${item.name}:${i}`} className="cv-merchant-checkout-card-view-17" style={{ marginTop: order.storeName || i > 0 ? 8 : 0 }}>
                <span className="cv-merchant-checkout-card-view-18" >
                  {item.name}
                  {item.specs && <span className="cv-merchant-checkout-card-view-19" >{item.specs}</span>}
                </span>
                <NumericText as="span" className="au-num cv-merchant-checkout-card-view-20" >×{item.quantity}</NumericText>
              </div>
            ))}
            {order.fulfillment && <div className="cv-merchant-checkout-card-view-21" >取餐方式 · {order.fulfillment}</div>}
          </div>
          {Array.isArray(card.spec_options) && card.spec_options.length > 0 && (
            // 规格 chips（demo-3ukshz #3）：只展示下单链消费得动的组（桥侧已按
            // _SPEC_GROUPS 过滤）；点非当前项发「在{店}点一杯{品}，要{规格}」重出预览
            <div className="cv-merchant-checkout-card-view-22" >
              {card.spec_options.map((group) => (
                <div key={group.name} className="cv-merchant-checkout-card-view-23" >
                  <span className="cv-merchant-checkout-card-view-24" >{group.name}</span>
                  {(group.options || []).map((opt) => {
                    if (!opt?.label) return null
                    const active = opt.label === group.selected
                    const action = specChipAction(card, group, opt.label)
                    const clickable = !active && !!onAction && !!action
                    return (
                      <button
                        key={opt.label}
                        type="button"
                        disabled={!clickable}
                        onClick={clickable ? () => onAction!(action!.send_text) : undefined}
                        className="cv-merchant-checkout-card-view-25" style={{ cursor: clickable ? 'pointer' : 'default', color: active ? 'var(--au-bg, #0d1117)' : 'var(--au-text-2)', background: active ? 'var(--au-primary)' : 'var(--au-fill)', border: `1px solid ${active ? 'var(--au-primary)' : 'var(--au-line-2)'}` }}
                      >
                        {opt.label}
                        {typeof opt.price_delta_cents === 'number' && opt.price_delta_cents > 0
                          ? ` +${(opt.price_delta_cents / 100).toFixed(opt.price_delta_cents % 100 ? 1 : 0)}元` : ''}
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          )}
          {(discount || order.amount) && (
            <div className="cv-merchant-checkout-card-view-26" >
              {discount && <div className="cv-merchant-checkout-card-view-27" ><span>优惠</span><span>-{discount}</span></div>}
              {order.amount && <div className="cv-merchant-checkout-card-view-28" style={{ marginTop: discount ? 4 : 0 }}><span className="cv-merchant-checkout-card-view-29" >实付</span><span className="cv-merchant-checkout-card-view-30" >{order.amount}</span></div>}
            </div>
          )}
          <MerchantActionRow buttons={regularButtons} onAction={onAction} />
        </>
      )}
    </div>
  )
}

// 停车费查询卡（只读，一分钱不动）
function ParkingFeeCardView({ card }: { card: any }) {
  return (
    <div className="au-card cv-parking-fee-card-view-1" >
      <div className="cv-parking-fee-card-view-2" >
        <span className="cv-parking-fee-card-view-3" >当前停车费</span>
        <span className="cv-parking-fee-card-view-4" >{card.amount}</span>
      </div>
      {card.plate && (
        <div className="cv-parking-fee-card-view-5" >车牌 {card.plate}</div>
      )}
    </div>
  )
}
