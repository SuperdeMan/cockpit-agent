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

// 商户卡动作的唯一渲染出口。onAction 复用 App.send，因此这些按钮始终是
// `is_confirmation=false` 的普通自然语言输入；创建/取消的写确认只在全局
// ConfirmBubble 中产生。按钮高度 44px，兼顾泊车触控。
function MerchantActionRow({ buttons, onAction }: {
  buttons: CardButton[]
  onAction?: (text: string) => void
}) {
  if (!buttons.length) return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
      {buttons.map((button) => {
        const destructive = /取消/.test(button.label)
        return (
          <button
            key={`${button.label}:${button.send_text}`}
            type="button"
            onClick={() => onAction?.(button.send_text)}
            disabled={!onAction}
            style={{
              minHeight: 44, padding: '9px 14px', borderRadius: 12,
              cursor: onAction ? 'pointer' : 'default', fontFamily: 'inherit',
              fontSize: 12.5, fontWeight: 650,
              color: destructive ? 'var(--au-warn)' : 'var(--au-text)',
              background: destructive ? 'rgba(245,158,11,0.09)' : 'var(--au-fill)',
              border: destructive ? '1px solid rgba(245,158,11,0.28)' : '1px solid var(--au-line-2)',
              opacity: onAction ? 1 : 0.58,
            }}
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
function Ico({ d, size = 12, color = 'currentColor', sw = 2, style }: { d: string | string[]; size?: number; color?: string; sw?: number; style?: CSSProperties }) {
  const paths = Array.isArray(d) ? d : [d]
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, ...style }}>{paths.map((p, i) => <path key={i} d={p} />)}</svg>
}
const IC_CHEVRON = 'm6 9 6 6 6-6'
const IC_EXT = ['M15 3h6v6', 'M10 14 21 3', 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6']
const IC_ALERT = ['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z', 'M12 9v4', 'M12 17h.01']
const IC_BOOK = ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z']
const IC_MAX = ['M15 3h6v6', 'M9 21H3v-6', 'M21 3l-7 7', 'M3 21l7-7']

// 置信度徽章（A-4 ConfBadge；§3-A 语义色，绝不虹彩）
const _CONF_TONE: Record<string, { c: string; bg: string; bd: string; label: string }> = {
  high: { c: 'var(--au-conf-high)', bg: 'rgba(70,214,224,0.11)', bd: 'rgba(70,214,224,0.26)', label: '高' },
  medium: { c: 'var(--au-conf-mid)', bg: 'rgba(245,158,11,0.11)', bd: 'rgba(245,158,11,0.26)', label: '中' },
  low: { c: 'var(--au-conf-low)', bg: 'rgba(107,114,128,0.11)', bd: 'rgba(107,114,128,0.24)', label: '未充分核实' },
}
function ConfPill({ level, small }: { level?: string; small?: boolean }) {
  if (!level) return null
  const t = _CONF_TONE[level] ?? _CONF_TONE.medium
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: small ? '2px 7px' : '3px 9px', borderRadius: 20, background: t.bg, border: `1px solid ${t.bd}`, flexShrink: 0 }}>
      <span style={{ width: small ? 5 : 6, height: small ? 5 : 6, borderRadius: '50%', background: t.c, flexShrink: 0 }} />
      <span style={{ fontSize: small ? 10 : 11.5, fontWeight: 600, color: t.c, whiteSpace: 'nowrap' }}>置信度 {t.label}</span>
    </span>
  )
}
// 从 url 取裸域名（去 www.），取不到回退 source
function domainOf(url?: string, fallback?: string): string {
  if (url) { try { return new URL(url).hostname.replace(/^www\./, '') } catch { /* 非法 url */ } }
  return fallback || ''
}

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
// 天气现象 → A-8 线性图标（雪/雾/霾/沙尘等未出图标，回落 emoji）。判定顺序：雷>雨>晴>云。
const WEATHER_GLYPH: Array<[string, IconName]> = [
  ['雷', 'weather-thunder-alert'], ['雨', 'weather-rain'], ['晴', 'weather-sunny'],
  ['多云', 'weather-cloudy'], ['阴', 'weather-cloudy'], ['云', 'weather-cloudy'],
]
function weatherGlyph(text: string, size: number, color = 'var(--au-text-2)') {
  for (const [k, n] of WEATHER_GLYPH) if (text.includes(k)) return <Icon name={n} size={size} color={color} />
  return <span style={{ fontSize: size * 0.92, lineHeight: 1 }}>{weatherIcon(text)}</span>
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
    default: return null
  }
}

// ─── 天气卡片 ───

// 数据真实性徽章（`_prov`，conventions §9.3）：mock=醒目琥珀「模拟数据」、degraded/cached=灰标、
// real=不打扰小字角标（来源 · 取数时间）。治理 P1 试点：weather / place 族 / search_result。
function ProvBadge({ prov }: { prov?: Provenance }) {
  if (!prov) return null
  const pill = (bg: string, fg: string, text: string, title?: string) => (
    <span title={title} style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999, background: bg, color: fg, whiteSpace: 'nowrap', flexShrink: 0 }}>{text}</span>
  )
  if (prov.mode === 'mock') return pill('rgba(245,158,11,0.16)', 'var(--au-warn)', '模拟数据', '演示用模拟数据，非真实来源')
  if (prov.mode === 'degraded') return pill('rgba(148,163,184,0.16)', 'var(--au-text-2)', prov.note ? `降级 · ${prov.note}` : '降级', '真实数据，但经降级路径取得')
  if (prov.mode === 'cached') return pill('rgba(148,163,184,0.16)', 'var(--au-text-2)', prov.note ? `缓存 · ${prov.note}` : '缓存')
  const t = (prov.fetched_at || '').replace('T', ' ').slice(5, 16)
  return (
    <span title="数据来源 · 取数时间" style={{ fontSize: 10, color: 'var(--au-text-3)', whiteSpace: 'nowrap', flexShrink: 0 }}>
      {prov.vendor}{t ? ` · ${t}` : ''}
    </span>
  )
}

function ManualCardView({ card }: { card: ManualCard }) {
  const images = manualImages(card) as ManualImage[]
  const chunks = (card.chunks || []).slice(0, 2)
  const title = card.document?.title || '车型用户手册'
  const revision = card.document?.revision || card._prov?.data_time || ''
  return (
    <div className="card card-evidence" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span style={{ width: 34, height: 34, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'rgba(70,214,224,0.10)', border: '1px solid rgba(70,214,224,0.22)', flexShrink: 0 }}>
          <Icon name="manual" size={18} color="var(--au-primary)" />
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 650, color: 'var(--au-text)' }}>{title}</div>
          <div style={{ marginTop: 3, fontSize: 10.5, color: 'var(--au-text-3)' }}>
            {[card.document?.vehicle_model, revision && `版本 ${revision}`].filter(Boolean).join(' · ')}
          </div>
        </div>
        <ProvBadge prov={card._prov} />
      </div>
      {images.length > 0 && (
        <>
          <CardHR />
          <div style={{ display: 'grid', gridTemplateColumns: images.length > 1 ? '1fr 1fr' : '1fr', gap: 10, padding: 12 }}>
            {images.map((image) => (
              <figure key={image.asset_id} style={{ margin: 0, minWidth: 0 }}>
                <div style={{ minHeight: image.role === 'warning_icon' ? 148 : 128, maxHeight: 250, borderRadius: 12, overflow: 'hidden', background: 'rgba(255,255,255,0.96)', border: '1px solid var(--au-line-2)', display: 'grid', placeItems: 'center' }}>
                  <img
                    src={image.data_uri}
                    alt={image.caption || '手册配图'}
                    loading="lazy"
                    style={{ display: 'block', width: image.role === 'warning_icon' ? 116 : '100%', height: image.role === 'warning_icon' ? 116 : 'auto', maxHeight: 250, objectFit: 'contain' }}
                  />
                </div>
                <figcaption style={{ marginTop: 7, display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 10.5, color: 'var(--au-text-3)' }}>
                  <span style={{ color: 'var(--au-text-2)' }}>{image.caption || '手册配图'}</span>
                  <span style={{ whiteSpace: 'nowrap' }}>PDF 第 {image.page_start} 页</span>
                </figcaption>
              </figure>
            ))}
          </div>
        </>
      )}
      {chunks.length > 0 && (
        <>
          <CardHR />
          <div style={{ padding: '11px 16px 13px', display: 'flex', flexDirection: 'column', gap: 9 }}>
            {chunks.map((chunk, index) => (
              <div key={`${chunk.page_start || 0}:${index}`} style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 10.5, fontWeight: 650, color: 'var(--au-primary)' }}>
                    {chunk.section_path?.slice(-1)[0] || `引用 ${index + 1}`}
                  </span>
                  {chunk.page_start ? <span style={{ fontSize: 10, color: 'var(--au-text-3)', whiteSpace: 'nowrap' }}>PDF 第 {chunk.page_start} 页</span> : null}
                </div>
                <p style={{ margin: 0, fontSize: 11.5, lineHeight: 1.65, color: 'var(--au-text-2)', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
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
  const upd = card.update_time && card.update_time !== 'mock'
    ? card.update_time.replace('T', ' ').replace(/\+.*/, '') : ''
  // 焦点日（问「明天/后天天气」）：主视觉切到该日预报，今天实况降为次行；遥测格换该日字段
  const focus = card.focus
  const tele: Array<{ icon: IconName; label: string; v: string | null; u: string }> = focus
    ? [
        { icon: 'humidity', label: '湿度', v: focus.humidity || null, u: '%' },
        { icon: 'wind', label: '风向', v: focus.wind_dir ? `${focus.wind_dir}${focus.wind_scale ? `${focus.wind_scale}级` : ''}` : null, u: '' },
        { icon: 'weather-rain', label: '降水', v: focus.precip || null, u: 'mm' },
        { icon: 'weather-sunny', label: '紫外线', v: focus.uv_index || null, u: '' },
        { icon: 'weather-cloudy', label: '夜间', v: focus.text_night || null, u: '' },
        { icon: 'temperature', label: '现在', v: card.temp ? `${card.temp}°C ${card.text}` : null, u: '' },
      ]
    : [
        { icon: 'temperature', label: '体感', v: card.feels_like || null, u: '°C' },
        { icon: 'humidity', label: '湿度', v: card.humidity || null, u: '%' },
        { icon: 'wind', label: '风向', v: card.wind_dir ? `${card.wind_dir}${card.wind_scale ? `${card.wind_scale}级` : ''}` : null, u: '' },
        { icon: 'visibility', label: '能见度', v: card.visibility || null, u: 'km' },
        { icon: 'weather-rain', label: '降水', v: card.precip || null, u: 'mm' },
        { icon: 'pressure', label: '气压', v: card.pressure || null, u: 'hPa' },
      ]
  const headGlyphText = focus ? focus.text_day : card.text
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* 预警 callout */}
      {alert && (
        <div style={{ padding: '10px 16px', background: 'rgba(245,158,11,0.11)', borderBottom: '1px solid rgba(245,158,11,0.20)', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <Icon name="warning" size={15} color="var(--au-warn)" style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--au-warn)' }}>{alert.headline}</div>
            <div style={{ fontSize: 11, color: 'var(--au-text-2)', marginTop: 2, lineHeight: 1.55 }}>{alert.detail}</div>
          </div>
        </div>
      )}
      {/* 头部：城市(+焦点日 chip) + 大温度/温度区间 + 天气文案 | 图标 + 更新 */}
      <div style={{ padding: '18px 18px 12px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>{card.city}</span>
            {focus && (
              <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: 'rgba(91,140,255,0.14)', color: 'var(--au-accent, #5B8CFF)', border: '1px solid rgba(91,140,255,0.28)' }}>{focus.label}</span>
            )}
          </div>
          {focus ? (
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6, lineHeight: 1 }}>
              <span className="au-num" style={{ fontSize: 54, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--au-text)' }}>{focus.temp_low}~{focus.temp_high}</span>
              <span className="au-num" style={{ fontSize: 22, fontWeight: 300, color: 'var(--au-text-2)', marginBottom: 4 }}>°C</span>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, lineHeight: 1 }}>
              <span className="au-num" style={{ fontSize: 68, fontWeight: 700, letterSpacing: '-0.03em', color: 'var(--au-text)' }}>{card.temp}</span>
              <span className="au-num" style={{ fontSize: 24, fontWeight: 300, color: 'var(--au-text-2)', marginTop: 10 }}>°C</span>
            </div>
          )}
          <div style={{ fontSize: 13.5, color: 'var(--au-text-2)', marginTop: 5 }}>
            {focus
              ? `${focus.text_day}${focus.text_night && focus.text_night !== focus.text_day ? `转${focus.text_night}` : ''}`
              : (card.text || '天气数据更新中')}
          </div>
        </div>
        <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 2 }}>
          <span style={{ lineHeight: 1, display: 'inline-flex', justifyContent: 'flex-end' }}>{weatherGlyph(headGlyphText, 40, 'var(--au-text)')}</span>
          {upd && <span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>更新 {upd}</span>}
          {card._prov && (
            <span style={{ display: 'inline-flex', justifyContent: 'flex-end' }}>
              <ProvBadge prov={card._prov} />
            </span>
          )}
        </div>
      </div>
      <CardHR />
      {/* telemetry 3×2 */}
      <div style={{ padding: '12px 13px', display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 7 }}>
        {tele.map((t) => {
          const miss = t.v == null
          return (
            <div key={t.label} style={{ padding: '9px 6px', borderRadius: 11, background: miss ? 'var(--au-fill)' : 'var(--au-fill)', border: '1px solid var(--au-line-2)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
              <Icon name={t.icon} size={17} state={miss ? 'disabled' : 'default'} />
              <span className="au-num" style={{ fontSize: 11.5, fontWeight: miss ? 400 : 600, color: miss ? 'var(--au-text-3)' : 'var(--au-text)', textAlign: 'center', lineHeight: 1.2 }}>{miss ? '—' : `${t.v}${t.u}`}</span>
              <span style={{ fontSize: 9.5, color: 'var(--au-text-3)' }}>{t.label}</span>
            </div>
          )
        })}
      </div>
      {/* 3 日预报（焦点日高亮） */}
      {!!card.forecast?.length && (
        <>
          <CardHR />
          <div style={{ padding: '13px 12px', display: 'flex' }}>
            {card.forecast.slice(0, 3).map((f, i) => {
              const isFocus = !!focus && f.date.slice(0, 10) === focus.date
              return (
                <div key={i} style={{ flex: 1, textAlign: 'center', padding: '6px 6px', borderRight: i < 2 ? '1px solid var(--au-line)' : 'none', borderRadius: isFocus ? 11 : 0, background: isFocus ? 'rgba(91,140,255,0.10)' : 'transparent' }}>
                  <div style={{ fontSize: 11, color: isFocus ? 'var(--au-accent, #5B8CFF)' : 'var(--au-text-3)', fontWeight: isFocus ? 700 : 400, marginBottom: 6 }}>{isFocus ? focus.label : (i === 0 ? '今天' : f.date.slice(5))}</div>
                  <div style={{ marginBottom: 4, lineHeight: 1, display: 'flex', justifyContent: 'center' }}>{weatherGlyph(f.text_day, 26)}</div>
                  <div style={{ fontSize: 11, color: 'var(--au-text-2)', marginBottom: 8 }}>{f.text_day}</div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4 }}>
                    <span className="au-num" style={{ fontSize: 11, color: '#93C5FD' }}>{f.temp_low}°</span>
                    <div style={{ flex: 1, height: 2.5, borderRadius: 2, background: 'linear-gradient(to right,rgba(91,140,255,.5),rgba(255,165,50,.5))' }} />
                    <span className="au-num" style={{ fontSize: 11, color: '#FCA5A5' }}>{f.temp_high}°</span>
                  </div>
                </div>
              )
            })}
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
                <div key={t.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 9, padding: '8px 10px', borderRadius: 11, background: 'var(--au-fill)', border: '1px solid var(--au-line-2)' }}>
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
            <div className="forecast-icon" style={{ display: 'flex', justifyContent: 'center' }}>{weatherGlyph(d.text_day, 22)}</div>
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
  // 市场标签优先用后端权威 market（腾讯 00700 是港股非 A 股）；缺失时按代码保守分类，不再硬编码 A股主板
  const marketTag = card.market || (() => {
    const d = (card.symbol || '').replace(/[^0-9]/g, '')
    if (d.length === 5) return '港股'
    if (d.length === 6) return d[0] === '6' ? '上证·A股' : (d[0] === '8' || d[0] === '4') ? '北证·A股' : '深证·A股'
    return card.symbol && !d ? '美股' : ''
  })()
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
            {marketTag && <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>· {marketTag}</span>}
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
      <div className="card-header" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><Icon name="newspaper" size={16} color="var(--au-text)" />{card.topic || '今日热点'}</div>
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
      <div className="card-header" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><Icon name="search" size={16} color="var(--au-text)" />{card.query}</div>
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
  const [open, setOpen] = useState(false)
  const sources = card.sources || []
  const shown = open ? sources : sources.slice(0, 3)
  const extra = sources.length - 3
  const fresh = relativeTime(card.freshness)
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <AIBadge label="AI · 联网搜索" />
          <ProvBadge prov={card._prov} />
        </div>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
              <Ico d={['m21 21-4.34-4.34', 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z']} size={13} color="var(--au-text-2)" />
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--au-text)' }}>{card.query}</span>
            </div>
            <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>找到 {sources.length} 条来源{fresh ? ` · 更新于${fresh}` : ''}</span>
          </div>
          <ConfPill level={card.confidence} />
        </div>
      </div>
      <CardHR />
      {shown.map((s, i) => {
        const dom = domainOf(s.url)
        return (
          <div key={i}>
            <div style={{ padding: '11px 16px', display: 'flex', gap: 11, alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, flexShrink: 0, marginTop: 1 }}>
                <span className="au-num" style={{ fontSize: 10, color: 'var(--au-text-3)', lineHeight: 1 }}>{i + 1}</span>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--au-primary)' }} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--au-text)' }}>{s.source || dom || '来源'}</span>
                  {dom && <span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>{dom}</span>}
                  <span style={{ fontSize: 10.5, color: 'var(--au-text-3)', marginLeft: 'auto' }}>{relativeTime(s.published) || s.published || ''}</span>
                </div>
                <p style={{ fontSize: 12, color: 'var(--au-text-2)', lineHeight: 1.6, margin: 0 }}>{s.title}</p>
              </div>
              {s.url && <a href={s.url} target="_blank" rel="noopener noreferrer" style={{ flexShrink: 0, marginTop: 2 }}><Ico d={IC_EXT} size={11} color="var(--au-text-3)" /></a>}
            </div>
            {i < shown.length - 1 && <div style={{ height: 1, background: 'var(--au-line)', margin: '0 16px' }} />}
          </div>
        )
      })}
      {extra > 0 && (
        <div style={{ padding: '10px 16px 13px', borderTop: '1px solid var(--au-line)', display: 'flex', justifyContent: 'flex-end' }}>
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
      <button onClick={onToggle} style={{ width: '100%', padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit' }}>
        <span style={{ width: 24, height: 24, borderRadius: 8, flexShrink: 0, background: open ? t.bg : 'var(--au-fill)', border: `1px solid ${open ? t.bd : 'var(--au-line-2)'}`, display: 'grid', placeItems: 'center', transition: 'all .22s' }}>
          <span className="au-num" style={{ fontSize: 10, fontWeight: 700, color: open ? t.c : 'var(--au-text-3)' }}>{String(idx).padStart(2, '0')}</span>
        </span>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: 600, color: open ? 'var(--au-text)' : 'var(--au-text-2)', transition: 'color .2s' }}>{heading}</span>
        <ConfPill level={confidence} small />
        <Ico d={IC_CHEVRON} size={14} color="var(--au-text-3)" style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .22s' }} />
      </button>
      {open && (
        <div style={{ padding: '2px 16px 14px 52px' }}>
          <p style={{ fontSize: 13, color: 'var(--au-text-2)', lineHeight: 1.8, margin: 0 }}>
            {body}
            {!!citations?.length && citations.map((c) => (
              <sup key={c} className="au-num" style={{ fontSize: '0.72em', fontWeight: 700, color: 'var(--au-primary)', marginLeft: 2 }}>[{c}]</sup>
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
    <div className="card card-research" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '16px 18px 14px' }}>
        <AIBadge label="AI · 深度调研报告" />
        <div style={{ display: 'flex', gap: 9, alignItems: 'flex-start', margin: '11px 0 12px' }}>
          <Ico d={IC_BOOK} size={14} color="var(--au-text-2)" style={{ marginTop: 2 }} />
          <span style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.38, color: 'var(--au-text)' }}>{card.question || '深度调研'}</span>
        </div>
        {card.summary && (
          <div style={{ padding: '11px 14px', borderRadius: 12, background: 'rgba(70,214,224,0.07)', border: '1px solid rgba(70,214,224,0.16)', marginBottom: 13 }}>
            <p style={{ fontSize: 13, color: 'var(--au-text)', lineHeight: 1.72, margin: 0 }}>{card.summary}</p>
          </div>
        )}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <ConfPill level={card.overall_confidence} />
          {fresh && <><span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>·</span><span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>时效 {fresh}</span></>}
          {sources.length > 0 && <><span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>·</span><span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>引用 {sources.length} 篇</span></>}
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
          <div style={{ padding: '12px 16px', textAlign: 'center' }}>
            <button onClick={() => setOpenSet(new Set(sections.map((_, i) => i)))} style={{ padding: '8px 22px', borderRadius: 20, background: 'rgba(70,214,224,0.08)', border: '1px solid rgba(70,214,224,0.22)', color: 'var(--au-primary)', fontSize: 12.5, fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit' }}>
              展开完整报告（共 {sections.length} 节）
            </button>
          </div>
        </>
      )}
      {gaps.length > 0 && (
        <>
          <CardHR />
          <div style={{ padding: '13px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <Ico d={IC_ALERT} size={13} color="var(--au-warn)" />
              <span style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--au-warn)' }}>未覆盖数据缺口</span>
            </div>
            {gaps.map((g, i) => (
              <div key={i} style={{ display: 'flex', gap: 9, alignItems: 'flex-start', marginBottom: i < gaps.length - 1 ? 7 : 0 }}>
                <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'rgba(245,158,11,0.55)', flexShrink: 0, marginTop: 6 }} />
                <span style={{ fontSize: 12, color: 'var(--au-text-2)', lineHeight: 1.65 }}>{g}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {sources.length > 0 && (
        <>
          <CardHR />
          <div style={{ padding: '13px 16px' }}>
            <div style={{ fontSize: 10.5, color: 'var(--au-text-3)', letterSpacing: '0.09em', textTransform: 'uppercase', fontWeight: 600, marginBottom: 10 }}>参考来源</div>
            {sources.map((r, i) => (
              <div key={i} style={{ display: 'flex', gap: 9, alignItems: 'flex-start', marginBottom: 8 }}>
                <sup className="au-num" style={{ fontSize: 9, fontWeight: 700, color: 'var(--au-primary)', flexShrink: 0, marginTop: 3.5, minWidth: 14 }}>[{r.idx ?? i + 1}]</sup>
                <div>
                  {r.url
                    ? <a href={r.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: 'var(--au-text-2)', textDecoration: 'none' }}>{r.title}</a>
                    : <span style={{ fontSize: 12, color: 'var(--au-text-2)' }}>{r.title}</span>}
                  <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}> — {[r.source, r.published].filter(Boolean).join(' · ') || domainOf(r.url)}</span>
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
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 新闻速览" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <Ico d={['M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2', 'M18 14h-8M15 18h-5M10 6h8v4h-8z']} size={13} color="var(--au-text-2)" />
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--au-text)' }}>{card.topic || '今日要闻'}</span>
          </div>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>已摘要 {items.length} 条</span>
        </div>
      </div>
      <CardHR />
      {shown.map((n, i) => {
        const rel = relativeTime(n.publish_time)
        return (
          <div key={i}>
            <div style={{ padding: '11px 16px', display: 'flex', gap: 11, alignItems: 'flex-start' }}>
              <span className="au-num" style={{ fontSize: 10.5, color: 'var(--au-text-3)', flexShrink: 0, marginTop: 1.5, minWidth: 16 }}>{String(i + 1).padStart(2, '0')}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                {n.url
                  ? <a href={n.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13, fontWeight: 600, color: 'var(--au-text)', lineHeight: 1.4, textDecoration: 'none' }}>{n.title}</a>
                  : <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--au-text)', lineHeight: 1.4 }}>{n.title}</div>}
                {n.summary && <p style={{ fontSize: 11.5, color: 'var(--au-text-2)', lineHeight: 1.68, margin: '5px 0' }}>{n.summary}</p>}
                {(n.source || rel) && (
                  <div style={{ display: 'flex', gap: 5, alignItems: 'center', marginTop: n.summary ? 0 : 5 }}>
                    {n.source && <span style={{ fontSize: 10.5, fontWeight: 500, color: 'var(--au-text-3)' }}>{n.source}</span>}
                    {rel && <><span style={{ fontSize: 10, color: 'var(--au-text-3)' }}>·</span><span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>{rel}</span></>}
                  </div>
                )}
              </div>
            </div>
            {i < shown.length - 1 && <div style={{ height: 1, background: 'var(--au-line)', margin: '0 16px' }} />}
          </div>
        )
      })}
      <div style={{ borderTop: '1px solid var(--au-line)', padding: '10px 16px 13px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          {srcCount > 0 ? (
            <button onClick={() => setShowSrc(!showSrc)} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, color: 'var(--au-text-2)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontFamily: 'inherit' }}>
              参考来源 {srcCount} 个
              <Ico d={IC_CHEVRON} size={12} color="var(--au-text-3)" style={{ transform: showSrc ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }} />
            </button>
          ) : <span />}
          {extra > 0 && <button className="ev-more" onClick={() => setOpen(!open)}>{open ? '收起' : `更多 ${extra} 条 ›`}</button>}
        </div>
        {showSrc && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 5 }}>
            {items.map((n, i) => {
              const rel = relativeTime(n.publish_time)
              return (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--au-primary)', flexShrink: 0 }} />
                  <span style={{ fontSize: 11, color: 'var(--au-text-2)' }}>{n.source}</span>
                  {rel && <span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>· {rel}</span>}
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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, minWidth: 0 }}>
      <div style={{ width: 48, height: 48, borderRadius: 12, display: 'grid', placeItems: 'center', background: `${color}22`, border: `2px solid ${color}55` }}>
        {/* 国家队显示国旗 emoji（.au-flag 套自托管国旗字体，Windows Chromium 也正常渲染）；无旗回落队名缩写 */}
        {flag
          ? <span className="au-flag" style={{ fontSize: 26, lineHeight: 1 }}>{flag}</span>
          : <span style={{ fontFamily: 'var(--au-font-mono)', fontSize: 14, fontWeight: 700, color }}>{name.slice(0, 2)}</span>}
      </div>
      <span style={{ fontSize: 12, fontWeight: 600, textAlign: 'center', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
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
    <div style={{ padding: '6px 0 4px' }}>
      {/* 计分板 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 8px 14px' }}>
        <TeamSquare name={f.home} color={HOME_C} flag={f.home_flag} />
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 3, minWidth: 80 }}>
          {scored ? (
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="au-num" style={{ fontSize: 30, fontWeight: 700, color: HOME_C, lineHeight: 1 }}>{f.home_goals}</span>
              <span className="au-num" style={{ fontSize: 18, fontWeight: 300, color: 'var(--au-text-3)' }}>–</span>
              <span className="au-num" style={{ fontSize: 30, fontWeight: 700, color: AWAY_C, lineHeight: 1 }}>{f.away_goals}</span>
            </span>
          ) : <span style={{ fontSize: 14, color: 'var(--au-text-3)' }}>{kickoff || 'VS'}</span>}
          <span style={{ fontSize: 10.5, fontWeight: f.status === 'live' ? 700 : 400, color: f.status === 'live' ? 'var(--au-warn)' : 'var(--au-text-3)' }}>
            {f.status === 'live' && f.elapsed ? `${f.status_text} ${f.elapsed}'` : f.status_text}
          </span>
        </div>
        <TeamSquare name={f.away} color={AWAY_C} flag={f.away_flag} />
      </div>
      {/* 进球时间线 */}
      {goals.length > 0 && (
        <div style={{ padding: '13px 16px', borderTop: '1px solid var(--au-line)' }}>
          <div style={{ fontSize: 10.5, color: 'var(--au-text-3)', letterSpacing: '0.09em', fontWeight: 600, marginBottom: 12 }}>进球时间线</div>
          {/* 90 分钟时间轴 + 进球标点 */}
          <div style={{ position: 'relative', height: 6, borderRadius: 3, background: 'var(--au-fill)', marginBottom: 14 }}>
            <div style={{ position: 'absolute', inset: 0, borderRadius: 3, background: `linear-gradient(to right,${HOME_C}40,${AWAY_C}30)` }} />
            {goals.map((g, i) => {
              const m = Math.min(parseInt(g.minute, 10) || 0, 90)
              const color = g.team === 'away' ? AWAY_C : HOME_C
              return <span key={i} style={{ position: 'absolute', left: `${(m / 90) * 100}%`, top: -3, transform: 'translateX(-50%)', width: 12, height: 12, borderRadius: '50%', background: color, border: '2px solid rgba(6,8,15,0.8)', boxShadow: `0 0 8px ${color}80` }} />
            })}
          </div>
          {/* 进球事件 */}
          {goals.map((g, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <span className="au-num" style={{ fontSize: 11, fontWeight: 700, color: g.team === 'away' ? AWAY_C : HOME_C, width: 28, textAlign: 'right', flexShrink: 0 }}>{g.minute}&apos;</span>
              <Icon name="sports" size={14} color="var(--au-text-2)" />
              <span style={{ fontSize: 12.5, color: 'var(--au-text)' }}>{g.player || '球员'}</span>
              {g.detail && g.detail !== '进球' && <span style={{ fontSize: 10, color: 'var(--au-text-3)', border: '1px solid var(--au-line-2)', borderRadius: 3, padding: '0 4px' }}>{g.detail}</span>}
              <span className="au-flag" style={{ fontSize: 11, color: 'var(--au-text-3)', marginLeft: 'auto' }}>{g.team === 'away' ? `${f.away_flag ? f.away_flag + ' ' : ''}${f.away}` : g.team === 'home' ? `${f.home_flag ? f.home_flag + ' ' : ''}${f.home}` : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SportsScoresCardView({ card }: { card: SportsScoresCard }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 赛事信息" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14.5, fontWeight: 600 }}><Icon name="sports" size={17} color="var(--au-text)" />{card.title}</span>
          {card.freshness && <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{relativeTime(card.freshness)}</span>}
        </div>
      </div>
      <CardHR />
      {card.fixtures.length === 0
        ? <div style={{ padding: 18, textAlign: 'center', fontSize: 12, color: 'var(--au-text-3)' }}>暂无比赛安排</div>
        : card.fixtures.map((f, i) => (
            <div key={i}>
              {i > 0 && <CardHR />}
              <FixtureBoard f={f} />
            </div>
          ))}
      {card.source && <div style={{ padding: '8px 16px 12px', fontSize: 10, color: 'var(--au-text-3)', fontFamily: 'var(--au-font-mono)' }}>数据来源 {card.source}</div>}
    </div>
  )
}

function SportsScorersCardView({ card }: { card: SportsScorersCard }) {
  return (
    <div className="card card-evidence card-sports">
      <AIBadge label="AI · 射手榜" />
      <div className="ev-head">
        <span className="ev-head-title" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}><Icon name="sports" size={16} color="var(--au-text)" />{card.title}</span>
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
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 路线规划" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14.5, fontWeight: 600, opacity: card.cancelled ? 0.55 : 1, textDecoration: card.cancelled ? 'line-through' : 'none' }}><Icon name="route-map" size={17} color="var(--au-text)" />{card.cancelled ? '导航已结束' : card.estimate ? '距离估算' : '规划路线'}</span>
          {(card.distance_km || dur) && <span className="au-num" style={{ fontSize: 12, color: 'var(--au-text-2)' }}>{dur}{card.distance_km ? `${dur ? ' · ' : ''}${card.distance_km}km` : ''}</span>}
        </div>
      </div>
      <CardHR />
      <div style={{ padding: '14px 20px' }}>
        {[
          { type: 'origin', icon: 'location' as IconName, label: card.origin || '当前位置', sub: '出发' },
          ...card.waypoints.map((w) => ({ type: 'stop', icon: 'pin' as IconName, label: w.name, sub: w.address || '途经点' })),
          { type: 'dest', icon: 'flag' as IconName, label: card.destination, sub: '目的地' },
        ].map((n, i, arr) => {
          const color = n.type === 'origin' ? 'var(--au-primary)' : n.type === 'dest' ? '#34D399' : '#F59E0B'
          return (
            <div key={i} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
                <span style={{ width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', background: `${n.type === 'stop' ? 'rgba(245,158,11,0.15)' : color}`, border: `2px solid ${color}` }}><Icon name={n.icon} size={15} color={n.type === 'stop' ? '#F59E0B' : '#06080F'} /></span>
                {i < arr.length - 1 && <span style={{ width: 1, height: 26, background: 'var(--au-line-2)', margin: '4px 0' }} />}
              </div>
              <div style={{ paddingTop: 4, flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, marginBottom: 2 }}>{n.label}</div>
                <div style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>{n.sub}</div>
              </div>
            </div>
          )
        })}
      </div>
      <div style={{ padding: '0 16px 14px' }}>
        {/* 按钮回发的必须是**可直接执行的自然语言**（I-031：「解释定位原理」那颗按钮回发后
            被判没听清）。estimate 卡上它才是真按钮——只算不导之后「那就导过去」是下一步；
            规划卡上导航已经发出去了，再点一次没有语义，保持原来的装饰态。 */}
        {card.cancelled
          ? <button onClick={() => onAction?.(`导航去${card.destination}`)} disabled={!onAction}
              style={{ width: '100%', padding: '11px 0', borderRadius: 14, background: 'transparent', border: '1px solid var(--au-line-2)', color: 'var(--au-text-2)', fontSize: 13.5, fontWeight: 600, cursor: onAction ? 'pointer' : 'default', opacity: onAction ? 1 : 0.58, fontFamily: 'inherit' }}>重新导航</button>
          : card.estimate
          ? <button onClick={() => onAction?.(`导航去${card.destination}`)} disabled={!onAction}
              style={{ width: '100%', padding: '11px 0', borderRadius: 14, background: 'var(--au-aurora)', border: 'none', color: '#fff', fontSize: 13.5, fontWeight: 600, cursor: onAction ? 'pointer' : 'default', opacity: onAction ? 1 : 0.58, fontFamily: 'inherit' }}>导航过去</button>
          : <button style={{ width: '100%', padding: '11px 0', borderRadius: 14, background: 'var(--au-aurora)', border: 'none', color: '#fff', fontSize: 13.5, fontWeight: 600, cursor: 'pointer' }}>开始导航</button>}
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
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 充电路线" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14.5, fontWeight: 600, color: 'var(--au-warn)' }}><Icon name="charging-station" size={17} color="var(--au-warn)" />充电路线规划</span>
          {card.distance_km ? <span className="au-num" style={{ fontSize: 12, color: 'var(--au-text-2)' }}>{card.distance_km}km{dur ? ` · ${dur}` : ''}</span> : null}
        </div>
      </div>
      <CardHR />
      {card.soc && <div style={{ padding: '12px 16px 10px' }}><SocBar soc={card.soc} dest={card.destination} /></div>}
      <CardHR />
      {card.stops.length > 0 ? (
        <div style={{ padding: '14px 18px' }}>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 10 }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--au-primary)', flexShrink: 0 }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>出发地</div>
              {card.soc && <div style={{ fontSize: 11, color: 'var(--au-text-3)' }}>当前电量 {card.soc}</div>}
            </div>
          </div>
          {card.stops.map((s, i) => (
            <div key={i}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '2px 0 2px 4px' }}>
                <span style={{ width: 1, height: 22, background: 'var(--au-line-2)' }} />
                {s.at_km != null && <span className="au-num" style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>约 {s.at_km}km 处</span>}
              </div>
              <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', marginBottom: 10, padding: '12px 14px', borderRadius: 14, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.20)' }}>
                <span style={{ width: 28, height: 28, borderRadius: 8, display: 'grid', placeItems: 'center', background: 'rgba(245,158,11,0.18)', border: '1px solid rgba(245,158,11,0.30)', flexShrink: 0 }}><Icon name="charging-station" size={15} color="#F59E0B" /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>{s.name}</div>
                  {s.address && <div style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{s.address}</div>}
                </div>
              </div>
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', padding: '0 0 0 4px' }}><span style={{ width: 1, height: 18, background: 'var(--au-line-2)' }} /></div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#34D399', flexShrink: 0 }} />
            <div style={{ fontSize: 13, fontWeight: 600 }}>{card.destination}</div>
          </div>
        </div>
      ) : (
        <div style={{ padding: '20px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ width: 36, height: 36, borderRadius: 10, display: 'grid', placeItems: 'center', background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.28)' }}><Icon name="check-circle" size={20} color="#34D399" /></span>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: '#34D399' }}>全程无需补电</div>
            <div style={{ fontSize: 11.5, color: 'var(--au-text-3)', marginTop: 2 }}>当前电量足以完成全程</div>
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
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 行程规划" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14.5, fontWeight: 600 }}><Icon name="calendar-trip" size={17} color="var(--au-text)" />{card.destination} · {card.days}日行程</span>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{card.status === 'confirmed' ? '已确认' : card.theme ? `《${card.theme}》主题` : '自驾 · AI 规划'}</span>
        </div>
      </div>
      <CardHR />
      <div style={{ padding: '8px 0 4px' }}>
        {days.map((day, di) => {
          const color = DAY_COLORS[di % DAY_COLORS.length]
          const charges = (day.legs || []).flatMap((l) => l.charging_stops || [])
          const isOpen = open.has(day.day_index)
          return (
            <div key={di}>
              {charges.length > 0 && (
                <div style={{ margin: '0 16px 6px', padding: '6px 12px', borderRadius: 10, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.18)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Icon name="charging-station" size={13} color="var(--au-warn)" />
                  <span style={{ fontSize: 11, color: 'var(--au-warn)' }}>途中补电 {charges.length} 次：{charges.map((c) => c.name).join('、')}</span>
                </div>
              )}
              <button onClick={() => toggle(day.day_index)} style={{ width: '100%', padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--au-text)', fontFamily: 'inherit' }}>
                <span style={{ width: 28, height: 20, borderRadius: 6, display: 'grid', placeItems: 'center', background: `${color}20`, border: `1px solid ${color}40`, fontFamily: 'var(--au-font-mono)', fontSize: 9.5, fontWeight: 700, color }}>D{day.day_index}</span>
                <span style={{ flex: 1, fontSize: 13, fontWeight: 600, textAlign: 'left' }}>{day.city ? `${day.city} · ` : ''}{day.theme || `第${day.day_index}天`}</span>
                {day.weather?.text && (
                  <span title={day.weather.text} style={{ display: 'flex', alignItems: 'center', gap: 3, fontSize: 11, color: 'var(--au-text-3)' }}>
                    {weatherGlyph(day.weather.text, 14)}
                    {day.weather.temp_low && day.weather.temp_high ? `${day.weather.temp_low}-${day.weather.temp_high}℃` : day.weather.text}
                  </span>
                )}
                <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{day.stops.length}个点</span>
                <span style={{ fontSize: 13, color: 'var(--au-text-3)', transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform .2s' }}>›</span>
              </button>
              <div style={{ maxHeight: isOpen ? 600 : 0, overflow: 'hidden', transition: 'max-height .3s ease' }}>
                {day.stops.map((s, i) => {
                  // 已接地的停靠点可点导航：派发整句『导航去第N天的X』→ 编排器路由 trip.navigate
                  const go = s.grounded && onAction ? () => onAction(`导航去第${day.day_index}天的${s.name}`) : undefined
                  return (
                    <div key={i} style={{ padding: '7px 16px 7px 52px', display: 'flex', alignItems: 'center', gap: 10 }}>
                      <Icon name={TRIP_STOP_ICON[s.type] || 'pin'} size={15} color="var(--au-text-2)" style={{ marginTop: 1 }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 12.5, fontWeight: 500, color: s.grounded ? 'var(--au-text)' : 'var(--au-text-2)' }}>{s.name}</div>
                        <div style={{ fontSize: 10.5, color: 'var(--au-text-3)', overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{s.grounded ? (s.poi?.address || '') : '待确认地点'}</div>
                      </div>
                      {go && (
                        <button onClick={go} style={{ padding: '3px 10px', borderRadius: 8, background: 'rgba(70,214,224,0.10)', border: '1px solid rgba(70,214,224,0.22)', fontSize: 10.5, color: 'var(--au-primary)', cursor: 'pointer', flexShrink: 0, fontFamily: 'inherit' }}>导航</button>
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
      <div style={{ padding: '11px 16px 13px', borderTop: '1px solid var(--au-line)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="voice-input" size={14} color="var(--au-text-3)" />
        <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>说「<span style={{ color: 'var(--au-text-2)' }}>下一站</span>」或「<span style={{ color: 'var(--au-text-2)' }}>导航去第 2 天的XX</span>」</span>
      </div>
    </div>
  )
}

// ─── POI 列表卡片 ───

function PoiListCardView({ card }: { card: PoiListCard }) {
  const isChoice = card.purpose === 'dest_choice' || card.purpose === 'waypoint_choice'
  const title = isChoice ? (card.title || '请选择') : `附近${card.keyword || '地点'}`
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 位置搜索" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14.5, fontWeight: 600 }}><Icon name="location" size={17} color="var(--au-text)" />{title}</span>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>已更新 · 共 {card.items.length} 个</span>
        </div>
      </div>
      <CardHR />
      {card.items.map((item, i) => (
        <div key={item.id || i}>
          <div style={{ padding: '12px 16px', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
            <span style={{ width: 26, height: 26, borderRadius: 8, flexShrink: 0, display: 'grid', placeItems: 'center', background: 'var(--au-line)', border: '1px solid var(--au-line-2)', fontFamily: 'var(--au-font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--au-text-2)' }}>{i + 1}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{item.name}</span>
                {(item.distance_km ?? 0) > 0 && <span className="au-num" style={{ fontSize: 12, color: 'var(--au-primary)', fontWeight: 600, flexShrink: 0 }}>{item.distance_km}km</span>}
              </div>
              {(item.rating ?? 0) > 0 && <div style={{ fontSize: 11, color: 'var(--au-warn)', marginBottom: 4 }}>★ {item.rating}</div>}
              {item.address && <div style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{item.address}</div>}
            </div>
          </div>
          {i < card.items.length - 1 && <div style={{ height: 1, background: 'var(--au-line)', margin: '0 16px' }} />}
        </div>
      ))}
      <div style={{ padding: '11px 16px 13px', borderTop: '1px solid var(--au-line)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="voice-input" size={14} color="var(--au-text-3)" />
        <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>说「<span style={{ color: 'var(--au-text-2)' }}>导航去第 2 个</span>」或「<span style={{ color: 'var(--au-text-2)' }}>最近的{card.keyword || '地点'}</span>」</span>
      </div>
    </div>
  )
}

// ─── POI 详情卡片 ───

function PoiDetailCardView({ card }: { card: PoiDetailCard }) {
  return (
    <div className="card card-poi-detail">
      <div className="poi-detail-name">{card.name}</div>
      {card.address && <div className="poi-detail-addr" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name="pin" size={13} color="var(--au-text-3)" />{card.address}</div>}
      <div className="poi-detail-row">
        {card.rating > 0 && <span>★ {card.rating}</span>}
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
    <div className="au-glass" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>{card.date_label || '我的提醒'}</span>
        <span style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>{total} 条</span>
      </div>
      {card.items.map((it) => (
        <div key={it.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="au-num" style={{ fontSize: 12.5, minWidth: 86, color: color(it.status) }}>{it.time_display}</span>
          <span style={{ fontSize: 13.5, flex: 1, textDecoration: it.status === 'done' ? 'line-through' : 'none', opacity: it.status === 'done' ? 0.55 : 1 }}>{it.title}</span>
          {it.recur_label && <span style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 10, color: 'var(--au-primary)', background: 'rgba(70,214,224,0.10)', border: '1px solid rgba(70,214,224,0.22)' }}>{it.recur_label}</span>}
          {it.status === 'fired' && <span style={{ fontSize: 10.5, color: '#F59E0B' }}>到点</span>}
        </div>
      ))}
      {(card.todos?.length || 0) > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, paddingTop: 6, borderTop: '1px solid var(--au-line-2)' }}>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)', width: '100%' }}>待办 · {card.todos!.length}</span>
          {card.todos!.map((t) => (
            <span key={t.id} className="au-glass" style={{ padding: '4px 10px', fontSize: 12, textDecoration: t.status === 'done' ? 'line-through' : 'none' }}>{t.title}</span>
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
      ...(fired ? { animation: 'au-proactive-pulse-amber 3s ease-in-out infinite', border: '1px solid rgba(245,158,11,0.35)' } : {}) }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: accent, boxShadow: `0 0 8px ${accent}` }} />
        <span style={{ fontSize: 12, color: 'var(--au-text-3)' }}>{label}</span>
        {it.recur_label && <span style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 10, color: 'var(--au-primary)', background: 'rgba(70,214,224,0.10)', border: '1px solid rgba(70,214,224,0.22)' }}>{it.recur_label}</span>}
        {it.time_display && <span className="au-num" style={{ marginLeft: 'auto', fontSize: 12.5, color: fired ? '#F59E0B' : 'var(--au-text-2)' }}>{it.time_display}</span>}
      </div>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{it.title}</div>
      {(fired || offer) && (card.actions?.length || 0) > 0 && (
        <div style={{ display: 'flex', gap: 8 }}>
          {card.actions!.map((a) => (
            <button key={a.label} onClick={() => onAction?.(a.send_text)} className="au-glass"
              style={{ padding: '7px 14px', fontSize: 12.5, cursor: 'pointer', border: '1px solid var(--au-line-2)', background: 'transparent', color: 'var(--au-text)' }}>
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
    suggest: { label: 'AI 建议', accent: '#F59E0B' },
  }
  const { label, accent } = meta[card.context] || meta.created
  const steps = card.actions_preview || []
  return (
    <div className="au-glass" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: accent, boxShadow: `0 0 8px ${accent}` }} />
        <span style={{ fontSize: 15, fontWeight: 600 }}>{card.name}</span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--au-text-3)' }}>{label}</span>
      </div>
      {card.description && (
        <div style={{ fontSize: 12.5, color: 'var(--au-text-3)', lineHeight: 1.5 }}>{card.description}</div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {steps.map((s, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span className="au-num" style={{ width: 18, height: 18, borderRadius: 6, flexShrink: 0, display: 'grid', placeItems: 'center', fontSize: 10.5, fontWeight: 700, background: 'var(--au-line)', color: 'var(--au-text-3)' }}>{i + 1}</span>
            <span style={{ fontSize: 13, flex: 1 }}>{s.label}</span>
            {s.danger && (
              <span style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 10, color: 'var(--au-warn)', background: 'rgba(245,158,11,0.10)', border: '1px solid rgba(245,158,11,0.25)' }}>需确认</span>
            )}
          </div>
        ))}
      </div>
      {(card.buttons?.length || 0) > 0 && (
        <div style={{ display: 'flex', gap: 8 }}>
          {card.buttons!.filter((b) => b.send_text).map((b) => (
            <button key={b.label} onClick={() => onAction?.(b.send_text)} className="au-glass"
              style={{ padding: '7px 14px', fontSize: 12.5, cursor: 'pointer', border: '1px solid var(--au-line-2)', background: 'transparent', color: 'var(--au-text)' }}>
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{title} · {items.length}</span>
      {items.map((s) => (
        <button key={s.id} onClick={onAction ? () => onAction(`开启${s.name}`) : undefined}
          style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left', padding: '9px 12px', borderRadius: 12, cursor: onAction ? 'pointer' : 'default', background: 'var(--au-fill)', border: '1px solid var(--au-line-2)', color: 'var(--au-text)' }}>
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{s.name}</span>
          {s.description && (
            <span style={{ fontSize: 11.5, color: 'var(--au-text-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.description}</span>
          )}
          {!!s.action_count && (
            <span className="au-num" style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 11, color: 'var(--au-text-3)' }}>{s.action_count} 步</span>
          )}
        </button>
      ))}
    </div>
  )
  return (
    <div className="au-glass" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {card.mine?.length > 0 && <Group title="我建的" items={card.mine} />}
      {card.builtin?.length > 0 && <Group title="内置" items={card.builtin} />}
      <div style={{ fontSize: 11, color: 'var(--au-text-3)' }}>说「创建钓鱼模式：座椅放平、氛围灯调暗」就能造一个</div>
    </div>
  )
}

// ─── R4.4 路由歧义澄清卡：一句提问 + 2~3 消歧选项（点/说「第N个」→ 回发 send_text 作新指令）───
function IntentChoiceCardView({ card, onAction }: { card: IntentChoiceCard; onAction?: (t: string) => void }) {
  const options = (card.options || []).filter((o) => o?.send_text)
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <AIBadge label="AI · 需要确认" />
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <Icon name="info" size={17} color="var(--au-primary)" style={{ marginTop: 2, flexShrink: 0 }} />
          <span style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.5 }}>{card.question}</span>
        </div>
      </div>
      <CardHR />
      <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {options.map((o, i) => (
          <button
            key={i}
            onClick={onAction ? () => onAction(o.send_text) : undefined}
            style={{
              display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left',
              padding: '11px 14px', borderRadius: 12, cursor: onAction ? 'pointer' : 'default',
              background: 'var(--au-fill)', border: '1px solid var(--au-line-2)', color: 'var(--au-text)',
            }}
          >
            <span style={{ width: 22, height: 22, borderRadius: 7, flexShrink: 0, display: 'grid', placeItems: 'center', background: 'var(--au-line)', fontFamily: 'var(--au-font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--au-primary)' }}>{i + 1}</span>
            <span style={{ fontSize: 13.5, fontWeight: 600 }}>{o.label}</span>
          </button>
        ))}
      </div>
      <div style={{ padding: '0 16px 12px', fontSize: 11, color: 'var(--au-text-3)' }}>说「第一个/第二个」或点选即可</div>
    </div>
  )
}

function PlaceListCardView({ card, onAction }: { card: PlaceListCard; onAction?: (t: string) => void }) {
  const title = `附近${card.keyword || card.category || '地点'}`
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <AIBadge label="AI · 周边发现" />
          <ProvBadge prov={card._prov} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 14.5, fontWeight: 600 }}><Icon name="location" size={17} color="var(--au-text)" />{title}</span>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>共 {card.items.length} 家</span>
        </div>
      </div>
      <CardHR />
      {card.items.map((item, i) => (
        <div key={item.id || i}>
          <div
            onClick={onAction ? () => onAction(`看${item.name}的详情`) : undefined}
            style={{ padding: '12px 16px', display: 'flex', gap: 12, alignItems: 'flex-start', cursor: onAction ? 'pointer' : 'default' }}
          >
            <span style={{ width: 26, height: 26, borderRadius: 8, flexShrink: 0, display: 'grid', placeItems: 'center', background: 'var(--au-line)', border: '1px solid var(--au-line-2)', fontFamily: 'var(--au-font-mono)', fontSize: 11, fontWeight: 700, color: 'var(--au-text-2)' }}>{i + 1}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>{item.name}</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  {(() => {
                    // 品牌门店（瑞幸/麦当劳）补「看菜单」直达：发现→看单→点单全程可点按
                    const menu = placeMenuAction(item.name)
                    return menu && onAction ? (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); onAction(menu.send_text) }}
                        style={{
                          padding: '3px 9px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                          fontFamily: 'inherit', cursor: 'pointer', color: 'var(--au-primary)',
                          background: 'transparent', border: '1px solid var(--au-primary)',
                        }}
                      >{menu.label}</button>
                    ) : null
                  })()}
                  {(item.distance_km ?? 0) > 0 && <span className="au-num" style={{ fontSize: 12, color: 'var(--au-primary)', fontWeight: 600 }}>{item.distance_km}km</span>}
                </span>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: item.address ? 4 : 0 }}>
                {(item.rating ?? 0) > 0 && <span style={{ fontSize: 11, color: 'var(--au-warn)', fontWeight: 600 }}>★ {item.rating}</span>}
                {item.cost && <span style={{ fontSize: 11, color: 'var(--au-text-2)' }}>人均 ¥{item.cost}</span>}
                {item.open_today && <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{item.open_today}</span>}
              </div>
              {item.address && <div style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{item.address}</div>}
              {item.tags && <div style={{ fontSize: 10.5, color: 'var(--au-text-3)', marginTop: 3 }}>{item.tags.split(/[,，]/).slice(0, 3).join(' · ')}</div>}
            </div>
          </div>
          {i < card.items.length - 1 && <div style={{ height: 1, background: 'var(--au-line)', margin: '0 16px' }} />}
        </div>
      ))}
      <div style={{ padding: '11px 16px 13px', borderTop: '1px solid var(--au-line)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Icon name="voice-input" size={14} color="var(--au-text-3)" />
        <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>说「<span style={{ color: 'var(--au-text-2)' }}>看第 1 个详情</span>」或「<span style={{ color: 'var(--au-text-2)' }}>导航去第 2 个</span>」</span>
      </div>
    </div>
  )
}

// ─── 周边发现详情卡（nearby.detail）───
function PlaceDetailRow({ icon, label, text }: { icon?: IconName; label?: string; text: string }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12, color: 'var(--au-text-2)' }}>
      {icon && <Icon name={icon} size={14} color="var(--au-text-3)" />}
      {label && <span style={{ color: 'var(--au-text-3)', flexShrink: 0, minWidth: 30 }}>{label}</span>}
      <span style={{ flex: 1, minWidth: 0 }}>{text}</span>
    </div>
  )
}

function PlaceDetailCardView({ card, onAction }: { card: PlaceDetailCard; onAction?: (t: string) => void }) {
  const hours = card.open_today || card.open_week
  const tel = (card.tel || '').split(/[;；/]/)[0].trim()
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '15px 16px 12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <AIBadge label="AI · 商户详情" />
          <ProvBadge prov={card._prov} />
        </div>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>{card.name}</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
          {(card.rating ?? 0) > 0 && <span style={{ fontSize: 12, color: 'var(--au-warn)', fontWeight: 700 }}>★ {card.rating}</span>}
          {card.cost && <span style={{ fontSize: 12, color: 'var(--au-text-2)' }}>人均 ¥{card.cost}</span>}
          {card.category && <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{card.category.split(/[;；]/)[0]}</span>}
        </div>
      </div>
      {card.photos && card.photos.length > 0 && (
        <div style={{ display: 'flex', gap: 6, padding: '0 16px 12px', overflowX: 'auto' }}>
          {card.photos.slice(0, 4).map((u, i) => (
            <img key={i} src={u} alt="" loading="lazy"
              onError={(e) => { e.currentTarget.style.display = 'none' }}
              style={{ width: 100, height: 72, objectFit: 'cover', borderRadius: 8, flexShrink: 0, border: '1px solid var(--au-line)' }} />
          ))}
        </div>
      )}
      <CardHR />
      <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {hours && <PlaceDetailRow icon="clock" text={hours} />}
        {tel && <PlaceDetailRow label="电话" text={card.tel!} />}
        {card.tags && <PlaceDetailRow label="特色" text={card.tags.split(/[,，]/).slice(0, 4).join(' · ')} />}
        {card.address && <PlaceDetailRow icon="pin" text={card.address} />}
      </div>
      <div style={{ display: 'flex', gap: 8, padding: '2px 16px 14px' }}>
        {onAction && (
          <button
            onClick={() => onAction(`导航去${card.name}`)}
            style={{ flex: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '9px 12px', borderRadius: 10, border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: '#fff', background: 'var(--au-primary)' }}
          >
            <Icon name="compass" size={15} color="#fff" />导航
          </button>
        )}
        {tel && (
          <a href={`tel:${tel}`}
            style={{ flex: 1, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '9px 12px', borderRadius: 10, textDecoration: 'none', fontSize: 13, fontWeight: 600, color: 'var(--au-text)', background: 'var(--au-line)', border: '1px solid var(--au-line-2)' }}
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
    <div className="au-card" style={{ padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 12, color: 'var(--au-text-3)' }}>{card.question || '看一看'}</span>
        {card.simulated && (
          <span style={{
            fontSize: 10, padding: '1px 6px', borderRadius: 6,
            background: 'var(--au-fill-2)', color: 'var(--au-text-3)',
          }}>模拟车外摄像头</span>
        )}
      </div>
      <div style={{ fontSize: 14, lineHeight: 1.6, color: 'var(--au-text)' }}>{card.answer}</div>
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
    <div className="au-card" style={{ padding: '14px 16px', maxWidth: 320 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--au-text)' }}>{presentation.title}</span>
        <span style={{ fontSize: 15, fontWeight: 800, color: 'var(--au-text)' }}>{card.amount}</span>
        <span style={{ flex: 1 }} />
        <ProvBadge prov={card._prov} />
      </div>
      {presentation.hasQr ? (
        <div style={{
          display: 'flex', justifyContent: 'center', padding: 10, borderRadius: 12,
          background: '#fff', opacity: expired ? 0.35 : 1, position: 'relative',
        }}>
          <img src={card.qr_svg} alt="付款码" style={{ width: 180, height: 180, display: 'block' }} />
          {expired && (
            <span style={{
              position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
              fontSize: 13, fontWeight: 700, color: '#7a3b06',
              background: 'rgba(245,158,11,0.92)', padding: '4px 12px', borderRadius: 8,
            }}>已过期</span>
          )}
        </div>
      ) : presentation.safeUrl && !expired ? (
        <div style={{
          display: 'grid', gridTemplateColumns: '1fr auto', gap: 8, padding: 10,
          borderRadius: 12, background: 'var(--au-fill)', border: '1px solid var(--au-line-2)',
        }}>
          <a
            href={presentation.safeUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              minHeight: 44, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              borderRadius: 10, textDecoration: 'none', background: 'rgba(70,214,224,0.12)',
              border: '1px solid rgba(70,214,224,0.28)', color: 'var(--au-primary)',
              fontSize: 12.5, fontWeight: 700,
            }}
          >打开安全支付链接</a>
          <button
            type="button"
            onClick={copyLink}
            style={{
              minHeight: 44, padding: '0 13px', borderRadius: 10, cursor: 'pointer',
              background: 'transparent', border: '1px solid var(--au-line-2)',
              color: copied ? 'var(--au-online)' : 'var(--au-text-2)',
              fontFamily: 'inherit', fontSize: 12,
            }}
          >{copied ? '已复制' : '复制链接'}</button>
        </div>
      ) : null}
      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--au-text-3)', textAlign: 'center' }}>
        {expired ? '支付入口已过期，请重新发起' :
          expiresAt > 0 ? `${presentation.hint} · ${mm}:${ss} 后过期` : presentation.hint}
      </div>
      {card.merchant_note && (
        <div style={{ marginTop: 4, fontSize: 11, color: 'var(--au-text-3)', textAlign: 'center' }}>
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
    <div className="au-card" style={{ padding: '12px 14px', maxWidth: 320 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span aria-hidden style={{
          width: 18, height: 18, borderRadius: 999, background: 'rgba(52,211,153,0.18)',
          color: '#34d399', fontSize: 12, fontWeight: 800, display: 'inline-flex',
          alignItems: 'center', justifyContent: 'center', flexShrink: 0,
        }}>✓</span>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--au-text)' }}>支付成功</span>
        {card.amount && <span style={{ fontSize: 14, fontWeight: 800, color: 'var(--au-text)' }}>{card.amount}</span>}
        <span style={{ flex: 1 }} />
        <ProvBadge prov={card._prov} />
      </div>
      <div style={{ marginTop: 6, fontSize: 11, color: 'var(--au-text-3)' }}>
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
    <div className="au-card" style={{ padding: '14px 16px', maxWidth: 340 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          fontSize: 10.5, fontWeight: 800, letterSpacing: '0.06em', padding: '3px 8px',
          borderRadius: 999, color: 'var(--au-primary)', background: 'rgba(70,214,224,0.10)',
          border: '1px solid rgba(70,214,224,0.22)',
        }}>
          {order.brand}
        </span>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--au-text)' }}>
          {card.type === 'mcp_order' ? '商户订单' : '商户服务'}
        </span>
        {card.demo && (
          <span style={{
            fontSize: 10, padding: '1px 6px', borderRadius: 6,
            background: 'rgba(245,158,11,0.16)', color: 'var(--au-warn)', fontWeight: 700,
          }}>{card.demo_label || '演示商户'}</span>
        )}
        <span style={{ flex: 1 }} />
        <ProvBadge prov={card._prov} />
      </div>
      <div style={{ marginTop: 12, padding: '10px 0', borderTop: '1px dashed var(--au-line-2)', borderBottom: '1px dashed var(--au-line-2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: 'var(--au-text-3)' }}>订单号</span>
          <span style={{ flex: 1 }} />
          {order.status && <span style={{ fontSize: 11, color: 'var(--au-warn)', flexShrink: 0 }}>{order.status}</span>}
        </div>
        <div
          className="au-num"
          data-order-id={order.orderId || undefined}
          title={order.orderId || undefined}
          style={{
            marginTop: 4, fontSize: 12, lineHeight: 1.35, fontWeight: 700,
            color: 'var(--au-text)', overflowWrap: 'anywhere', wordBreak: 'break-all',
          }}
        >
          {order.orderId || '待商户回传'}
        </div>
        {order.storeName && <div style={{ marginTop: 6, fontSize: 12, color: 'var(--au-text-2)' }}>门店 · {order.storeName}</div>}
        {order.fulfillment && <div style={{ marginTop: 4, fontSize: 11.5, color: 'var(--au-text-3)' }}>取餐 · {order.fulfillment}</div>}
        {order.items.map((item, i) => (
          <div key={`${item.name}:${i}`} style={{ display: 'flex', gap: 8, marginTop: 7, color: 'var(--au-text-2)' }}>
            <span style={{ flex: 1, minWidth: 0, fontSize: 12.5 }}>{item.name}{item.specs ? <span style={{ color: 'var(--au-text-3)' }}> · {item.specs}</span> : null}</span>
            <span className="au-num" style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>×{item.quantity}</span>
          </div>
        ))}
        {(sku || size) && !order.items.length && (
          <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--au-text-3)' }}>
            {sku}{size ? ` · ${size}` : ''}
          </div>
        )}
      </div>
      {(order.amount || duplicate) && (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 10 }}>
          {order.amount && <><span style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>应付</span><span style={{ fontSize: 18, fontWeight: 850, color: 'var(--au-text)' }}>{order.amount}</span></>}
          <span style={{ flex: 1 }} />
          {duplicate && <span style={{ fontSize: 10.5, color: 'var(--au-text-3)' }}>已有订单 · 幂等命中</span>}
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
    <div className="au-card" style={{ padding: '14px 16px', maxWidth: 340 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          fontSize: 10.5, fontWeight: 800, letterSpacing: '0.06em', padding: '3px 8px',
          borderRadius: 999, color: 'var(--au-primary)', background: 'rgba(70,214,224,0.10)',
          border: '1px solid rgba(70,214,224,0.22)',
        }}>
          {order.brand}
        </span>
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--au-text)' }}>商户信息</span>
        {card.demo && (
          <span style={{
            fontSize: 10, padding: '1px 6px', borderRadius: 6,
            background: 'rgba(245,158,11,0.16)', color: 'var(--au-warn)', fontWeight: 700,
          }}>{card.demo_label || '演示商户'}</span>
        )}
        <span style={{ flex: 1 }} />
        <ProvBadge prov={card._prov} />
      </div>
      {typeof card.tool === 'string' && card.tool && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--au-text-3)' }}>来源 · {card.tool}</div>
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
    <div className="au-card" style={{ padding: '15px 16px', maxWidth: 360 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
          background: 'var(--au-primary)', boxShadow: '0 0 10px rgba(70,214,224,0.55)',
        }} />
        <span style={{ fontSize: 14.5, fontWeight: 750, color: 'var(--au-text)' }}>{title}</span>
        <span style={{ flex: 1 }} />
        <ProvBadge prov={card._prov} />
      </div>

      {isChoices ? (
        <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
          {typeof card.total === 'number' && card.total > optionButtons.length && (
            <div style={{ fontSize: 11, color: 'var(--au-text-3)' }}>
              在售共 {card.total} 款，这里是一页——按分类看或直接报名字
            </div>
          )}
          {Array.isArray(card.categories) && card.categories.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {card.categories.map((cat) => (cat?.label && cat?.send_text ? (
                <button
                  key={cat.label}
                  type="button"
                  disabled={!onAction}
                  onClick={onAction ? () => onAction(cat.send_text!) : undefined}
                  style={{
                    padding: '3px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                    fontFamily: 'inherit', cursor: onAction ? 'pointer' : 'default',
                    color: 'var(--au-primary)', background: 'transparent',
                    border: '1px solid var(--au-primary)',
                  }}
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
                style={{
                  minHeight: 52, width: '100%', padding: '9px 12px', borderRadius: 12,
                  display: 'flex', flexDirection: 'row', justifyContent: 'flex-start', alignItems: 'center', gap: 10,
                  textAlign: 'left', cursor: onAction ? 'pointer' : 'default', fontFamily: 'inherit',
                  color: 'var(--au-text)', background: 'var(--au-fill)', border: '1px solid var(--au-line-2)',
                }}
              >
                {image && (
                  // 图加载失败就把自己摘掉——车机上一张裂图比没有图更糟
                  <img
                    src={image}
                    alt=""
                    loading="lazy"
                    referrerPolicy="no-referrer"
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none' }}
                    style={{
                      width: 40, height: 40, flexShrink: 0, borderRadius: 8,
                      objectFit: 'cover', background: 'var(--au-line-2)',
                    }}
                  />
                )}
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                  <span style={{ fontSize: 13, fontWeight: 700 }}>{button.label}</span>
                  {option?.subtitle && <span style={{ marginTop: 3, fontSize: 11, color: 'var(--au-text-3)' }}>{option.subtitle}</span>}
                </span>
              </button>
            )
          })}
        </div>
      ) : (
        <>
          <div style={{ marginTop: 12, padding: '10px 0', borderTop: '1px dashed var(--au-line-2)', borderBottom: '1px dashed var(--au-line-2)' }}>
            {order.storeName && <div style={{ fontSize: 12.5, color: 'var(--au-text-2)' }}>门店 · {order.storeName}</div>}
            {order.items.map((item, i) => (
              <div key={`${item.name}:${i}`} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginTop: order.storeName || i > 0 ? 8 : 0 }}>
                <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: 'var(--au-text)' }}>
                  {item.name}
                  {item.specs && <span style={{ display: 'block', marginTop: 2, fontSize: 11.5, color: 'var(--au-text-3)' }}>{item.specs}</span>}
                </span>
                <span className="au-num" style={{ fontSize: 12, color: 'var(--au-text-2)' }}>×{item.quantity}</span>
              </div>
            ))}
            {order.fulfillment && <div style={{ marginTop: 8, fontSize: 11.5, color: 'var(--au-text-3)' }}>取餐方式 · {order.fulfillment}</div>}
          </div>
          {Array.isArray(card.spec_options) && card.spec_options.length > 0 && (
            // 规格 chips（demo-3ukshz #3）：只展示下单链消费得动的组（桥侧已按
            // _SPEC_GROUPS 过滤）；点非当前项发「在{店}点一杯{品}，要{规格}」重出预览
            <div style={{ marginTop: 10, display: 'grid', gap: 7 }}>
              {card.spec_options.map((group) => (
                <div key={group.name} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 11, color: 'var(--au-text-3)', flexShrink: 0, minWidth: 28 }}>{group.name}</span>
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
                        style={{
                          padding: '3px 10px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                          fontFamily: 'inherit', cursor: clickable ? 'pointer' : 'default',
                          color: active ? 'var(--au-bg, #0d1117)' : 'var(--au-text-2)',
                          background: active ? 'var(--au-primary)' : 'var(--au-fill)',
                          border: `1px solid ${active ? 'var(--au-primary)' : 'var(--au-line-2)'}`,
                        }}
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
            <div style={{ marginTop: 10 }}>
              {discount && <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--au-text-3)' }}><span>优惠</span><span>-{discount}</span></div>}
              {order.amount && <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginTop: discount ? 4 : 0 }}><span style={{ fontSize: 12, color: 'var(--au-text-2)' }}>实付</span><span style={{ fontSize: 20, fontWeight: 850, color: 'var(--au-text)' }}>{order.amount}</span></div>}
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
    <div className="au-card" style={{ padding: '12px 14px', maxWidth: 320 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 13, color: 'var(--au-text-2)' }}>当前停车费</span>
        <span style={{ fontSize: 18, fontWeight: 800, color: 'var(--au-text)' }}>{card.amount}</span>
      </div>
      {card.plate && (
        <div style={{ marginTop: 4, fontSize: 11, color: 'var(--au-text-3)' }}>车牌 {card.plate}</div>
      )}
    </div>
  )
}
