// 右上下文舞台（P1 新增）——横屏带来的最大设计机会：随对话切换"场景"，让"此刻的车"在场。
// 场景由最近一条卡片/意图推导：天气卡→天气场景（呼应 A-2）；出行类卡→地图场景（呼应 A-5）；
// 否则回落待机场景（时钟 + 车辆概览 + 氛围）。媒体/车况场景待 HMI 侧补取数（P1 先占位）。
import { useEffect, useMemo, useState } from 'react'
import { useSettings } from '../settings'
import { AuroraOrb } from './aurora'
import type { Msg, UiCard, WeatherCard, PoiListCard, PoiDetailCard, RoutePlanCard, ChargingRouteCard, TripItineraryCard } from '../types'

type Scene =
  | { kind: 'idle' }
  | { kind: 'weather'; card: WeatherCard }
  | { kind: 'map'; card: UiCard }

const MAP_TYPES = ['poi_list', 'poi_detail', 'route_plan', 'charging_route', 'trip_itinerary']

function flatten(card?: UiCard): UiCard[] {
  if (!card) return []
  if (card.type === 'card_group') return card.items.flatMap(flatten)
  return [card]
}

function deriveScene(messages: Msg[]): Scene {
  for (let i = messages.length - 1; i >= 0; i--) {
    for (const c of flatten(messages[i].uiCard)) {
      if (c.type === 'weather') return { kind: 'weather', card: c as WeatherCard }
      if (MAP_TYPES.includes(c.type)) return { kind: 'map', card: c }
    }
  }
  return { kind: 'idle' }
}

export function ContextualStage({ messages }: { messages: Msg[] }) {
  const scene = deriveScene(messages)
  return (
    <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(120% 120% at 70% 20%, rgba(91,140,255,0.10), transparent 60%)' }}>
      {scene.kind === 'weather' ? (
        <WeatherStage card={scene.card} />
      ) : scene.kind === 'map' ? (
        <MapStage card={scene.card} />
      ) : (
        <IdleStage />
      )}
    </div>
  )
}

// ── 待机场景：时钟 + 日期 + 车辆概览 + 光球氛围 ──
function IdleStage() {
  const { settings } = useSettings()
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000 * 10)
    return () => clearInterval(t)
  }, [])
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const week = '日一二三四五六'[now.getDay()]
  const date = `${now.getMonth() + 1}月${now.getDate()}日 · 周${week}`

  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 28, padding: 40 }}>
      <AuroraOrb size={120} state="idle" />
      <div style={{ textAlign: 'center' }}>
        <div className="au-num" style={{ fontSize: 84, fontWeight: 700, letterSpacing: '-0.03em', lineHeight: 1 }}>
          {hh}<span style={{ opacity: 0.5 }}>:</span>{mm}
        </div>
        <div style={{ fontSize: 15, color: 'var(--au-text-2)', marginTop: 10 }}>{date}</div>
      </div>
      {/* 车辆概览（占位 mock，待 HMI 侧接车况取数）*/}
      <div style={{ display: 'flex', gap: 14 }}>
        {[
          { label: '电量', value: '62', unit: '%' },
          { label: '续航', value: '430', unit: 'km' },
          { label: '挡位', value: 'P', unit: '' },
        ].map((m) => (
          <div key={m.label} className="au-glass" style={{ padding: '16px 22px', textAlign: 'center', minWidth: 96 }}>
            <div className="au-num" style={{ fontSize: 26, fontWeight: 700 }}>
              {m.value}<span style={{ fontSize: 13, fontWeight: 400, color: 'var(--au-text-2)', marginLeft: 2 }}>{m.unit}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--au-text-3)', marginTop: 4 }}>{m.label}</div>
          </div>
        ))}
      </div>
      <div style={{ fontSize: 13, color: 'var(--au-text-3)' }}>我是{settings.assistantName}，随时为你待命</div>
    </div>
  )
}

// ── 天气场景（照 A-2 右舞台）：活的场景——雨丝 + 浮动大温度 + 玻璃指标芯片 + 底部信息条 + 极光边 ──
const Sep = () => <div style={{ width: 1, height: 16, background: 'var(--au-line-2)' }} />
function StripItem({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', gap: 7, alignItems: 'center' }}>
      <span style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>{label}</span>
      <span className="au-num" style={{ fontSize: 13, color }}>{value}</span>
    </div>
  )
}

function WeatherStage({ card }: { card: WeatherCard }) {
  const rainy = /雨|阵雨|雷|雪/.test(card.text || '')
  // 确定性雨丝（不用 random，稳定且可截图）
  const rain = useMemo(
    () => Array.from({ length: 26 }, (_, i) => ({
      x: (i * 37 + 11) % 99,
      h: 12 + ((i * 7) % 12),
      delay: ((i * 37) % 34) / 10,
      dur: 1.1 + ((i * 13) % 9) / 10,
      op: 0.25 + ((i * 7) % 40) / 100,
    })),
    [],
  )
  const today = card.forecast?.[0]
  const chips: Array<{ icon: string; label: string; value: string }> = []
  if (card.humidity) chips.push({ icon: '💧', label: '湿度', value: `${card.humidity}%` })
  if (card.wind_dir) chips.push({ icon: '🌬️', label: '风', value: `${card.wind_dir}${card.wind_scale ? ` ${card.wind_scale}级` : ''}` })
  if (card.air_quality) chips.push({ icon: '🌿', label: '空气质量', value: `${card.air_quality.category} ${card.air_quality.aqi}` })
  if (card.visibility) chips.push({ icon: '👁', label: '能见度', value: `${card.visibility}km` })

  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden' }}>
      {/* AI 上下文激活——屏幕边缘极光（§5）*/}
      <div style={{ position: 'absolute', inset: 0, borderRadius: 'var(--au-r-3xl)', border: '1.5px solid transparent', background: 'linear-gradient(rgba(0,0,0,0),rgba(0,0,0,0)) padding-box, var(--au-aurora) border-box', animation: 'au-edge-pulse 3.5s ease-in-out infinite', pointerEvents: 'none', zIndex: 6 }} />
      {/* 柔云氛围 */}
      <div style={{ position: 'absolute', top: '10%', left: '14%', width: 380, height: 150, borderRadius: '50%', background: 'radial-gradient(circle, rgba(140,175,255,0.10), transparent 70%)', filter: 'blur(22px)' }} />
      <div style={{ position: 'absolute', top: '34%', right: '10%', width: 260, height: 120, borderRadius: '50%', background: 'radial-gradient(circle, rgba(140,175,255,0.08), transparent 70%)', filter: 'blur(22px)' }} />
      {/* 雨丝 */}
      {rainy && rain.map((d, i) => (
        <div key={i} aria-hidden style={{ position: 'absolute', left: `${d.x}%`, top: 0, width: 1.5, height: d.h, borderRadius: 1, background: `linear-gradient(to bottom, transparent, rgba(70,214,224,${d.op}))`, animation: `au-rain ${d.dur}s ${d.delay}s linear infinite`, pointerEvents: 'none' }} />
      ))}
      {/* 主显示——浮动 */}
      <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-52%)', textAlign: 'center', animation: 'au-temp-float 7s ease-in-out infinite' }}>
        <div style={{ fontSize: 16, fontWeight: 300, letterSpacing: '0.5em', color: 'var(--au-text-2)', marginBottom: 12, paddingLeft: '0.5em' }}>{card.city}</div>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', gap: 6, marginBottom: 14 }}>
          <span className="au-num" style={{ fontSize: 'clamp(96px,11vw,150px)', fontWeight: 700, letterSpacing: '-0.04em', lineHeight: 1, textShadow: '0 0 80px rgba(91,233,255,0.12)' }}>{card.temp}</span>
          <span className="au-num" style={{ fontSize: 36, fontWeight: 300, color: 'var(--au-text-2)', marginTop: '0.9em' }}>°C</span>
        </div>
        <div style={{ fontSize: 24, fontWeight: 500, marginBottom: 28 }}>{card.text}</div>
        {chips.length > 0 && (
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
            {chips.map((s) => (
              <div key={s.label} className="au-glass" style={{ padding: '10px 16px', textAlign: 'center', minWidth: 86 }}>
                <div style={{ fontSize: 18, marginBottom: 4 }}>{s.icon}</div>
                <div className="au-num" style={{ fontSize: 13.5, fontWeight: 600 }}>{s.value}</div>
                <div style={{ fontSize: 11, color: 'var(--au-text-3)', marginTop: 2 }}>{s.label}</div>
              </div>
            ))}
          </div>
        )}
      </div>
      {/* 底部信息条 */}
      <div style={{ position: 'absolute', bottom: 20, left: '50%', transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 18, padding: '10px 26px', borderRadius: 30, background: 'rgba(6,8,15,0.55)', WebkitBackdropFilter: 'blur(20px)', backdropFilter: 'blur(20px)', border: '1px solid var(--au-line-2)', whiteSpace: 'nowrap', zIndex: 5 }}>
        {today && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span style={{ fontSize: 11.5, color: 'var(--au-text-3)' }}>今日</span>
            <span className="au-num" style={{ fontSize: 13, color: '#93C5FD' }}>{today.temp_low}°</span>
            <div style={{ width: 50, height: 3, borderRadius: 2, background: 'linear-gradient(to right, rgba(91,140,255,0.55), rgba(255,165,50,0.55))' }} />
            <span className="au-num" style={{ fontSize: 13, color: '#FCA5A5' }}>{today.temp_high}°</span>
          </div>
        )}
        {card.precip && <><Sep /><StripItem label="降水" value={`${card.precip}mm`} color="var(--au-primary)" /></>}
        {card.air_quality && <><Sep /><StripItem label="空气质量" value={`${card.air_quality.category} ${card.air_quality.aqi}`} color="#A3E635" /></>}
        {rainy && <><Sep /><span style={{ fontSize: 12, color: 'var(--au-text-2)' }}>☂️ 建议带伞</span></>}
      </div>
    </div>
  )
}

// ── 地图场景（呼应 A-5 右舞台）：路网底纹 SVG + 按卡类型的数据驱动「示意图」可视化 ──
// 真实卡多无经纬度，故用示意图语言（非真实地理）：POI 按距离环布 + 测距虚环；路线沿对角折线流动虚线；
// 充电按 at_km 比例落补电站 + SoC 条；行程按天连点。坐标系 viewBox 600×480，slice 充满右栏（响应式）。
// 真实地图 SDK 留实现期（见实施计划 §4 非目标）。
const VB_W = 600
const VB_H = 480
const sx = (p: number) => (p / 100) * VB_W
const sy = (p: number) => (p / 100) * VB_H
const ROADS_H = [18, 32, 44, 56, 67, 78, 88]
const ROADS_V = [15, 28, 42, 55, 68, 80, 91]

function mapMeta(card: UiCard): { label: string; summary?: string[]; foot: string } {
  switch (card.type) {
    case 'poi_list': { const c = card as PoiListCard; return { label: c.title || c.keyword || '附近地点', foot: `${c.items.length} 个地点` } }
    case 'poi_detail': { const c = card as PoiDetailCard; return { label: '地点详情', foot: c.category || '' } }
    case 'route_plan': { const c = card as RoutePlanCard; const s: string[] = []; if (c.distance_km != null) s.push(`${c.distance_km}km`); if (c.duration_min != null) s.push(`${c.duration_min}分钟`); return { label: '行驶路线', summary: s.length ? s : undefined, foot: c.destination } }
    case 'charging_route': { const c = card as ChargingRouteCard; const s: string[] = []; if (c.distance_km != null) s.push(`${c.distance_km}km`); if (c.duration_min != null) s.push(`${c.duration_min}分钟`); return { label: '充电路线', summary: s.length ? s : undefined, foot: `→ ${c.destination}` } }
    case 'trip_itinerary': { const c = card as TripItineraryCard; return { label: '行程地图', summary: [`${c.days}天`, `${c.itinerary?.length || 0}段`], foot: c.destination } }
    default: return { label: '地图', foot: '' }
  }
}

function MapStage({ card }: { card: UiCard }) {
  const meta = mapMeta(card)
  return (
    <div style={{ position: 'absolute', inset: 0, borderRadius: 'var(--au-r-3xl)', overflow: 'hidden', background: 'linear-gradient(158deg,#06080F 0%,#0B1020 60%,#080D18 100%)' }}>
      <div aria-hidden style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
        <span style={{ position: 'absolute', top: '10%', left: '18%', width: 260, height: 220, borderRadius: '50%', background: 'radial-gradient(circle,rgba(91,140,255,0.15),transparent 70%)', filter: 'blur(44px)' }} />
        <span style={{ position: 'absolute', bottom: '14%', right: '14%', width: 220, height: 180, borderRadius: '50%', background: 'radial-gradient(circle,rgba(91,233,255,0.10),transparent 70%)', filter: 'blur(50px)' }} />
      </div>

      <svg width="100%" height="100%" viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid slice" style={{ position: 'absolute', inset: 0, fontFamily: 'var(--au-font-ui)' }}>
        {ROADS_H.map((y, i) => <line key={'h' + i} x1={0} y1={sy(y)} x2={VB_W} y2={sy(y)} stroke="rgba(91,140,255,0.055)" strokeWidth={1} />)}
        {ROADS_V.map((x, i) => <line key={'v' + i} x1={sx(x)} y1={0} x2={sx(x)} y2={VB_H} stroke="rgba(91,140,255,0.055)" strokeWidth={1} />)}
        <line x1={sx(15)} y1={sy(18)} x2={sx(42)} y2={sy(44)} stroke="rgba(91,140,255,0.05)" strokeWidth={1} />
        <line x1={sx(55)} y1={sy(32)} x2={sx(80)} y2={sy(56)} stroke="rgba(91,140,255,0.05)" strokeWidth={1} />
        <line x1={sx(68)} y1={sy(18)} x2={sx(91)} y2={sy(44)} stroke="rgba(91,140,255,0.05)" strokeWidth={1} />

        {card.type === 'poi_list' && <PoiView card={card as PoiListCard} />}
        {card.type === 'poi_detail' && <PoiDetailView card={card as PoiDetailCard} />}
        {card.type === 'route_plan' && <PathView card={card as RoutePlanCard} />}
        {card.type === 'charging_route' && <ChargeView card={card as ChargingRouteCard} />}
        {card.type === 'trip_itinerary' && <ItineraryView card={card as TripItineraryCard} />}
      </svg>

      <div style={{ position: 'absolute', top: 18, left: 18, padding: '5px 13px', borderRadius: 20, background: 'rgba(70,214,224,0.10)', border: '1px solid rgba(70,214,224,0.22)', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--au-primary)', boxShadow: '0 0 8px var(--au-primary)' }} />
        <span style={{ fontSize: 12.5, color: 'var(--au-primary)', fontWeight: 500 }}>{meta.label}</span>
      </div>
      {meta.summary && meta.summary.length > 0 && (
        <div className="au-glass" style={{ position: 'absolute', top: 18, right: 18, padding: '7px 14px', display: 'inline-flex', gap: 10, alignItems: 'center' }}>
          {meta.summary.map((s, i) => <span key={i} className="au-num" style={{ fontSize: 12.5, color: i === 0 ? 'var(--au-text)' : 'var(--au-text-2)' }}>{s}</span>)}
        </div>
      )}
      <div style={{ position: 'absolute', bottom: 16, right: 20, fontSize: 11, color: 'var(--au-text-3)', fontFamily: 'var(--au-font-mono)' }}>{meta.foot || '地图示意 · 实现期接 SDK'}</div>
    </div>
  )
}

// POI：测距虚环 + 当前位置 + 编号标点（连线呼应左卡「第N个」）
function PoiView({ card }: { card: PoiListCard }) {
  const items = card.items.slice(0, 6)
  const n = items.length
  const cx = sx(50), cy = sy(50)
  const pts = items.map((it, i) => {
    const ang = (-90 + (360 / Math.max(n, 1)) * i) * (Math.PI / 180)
    const dist = it.distance_km ?? 1.5 + i
    const rr = 20 + Math.min(dist, 6) / 6 * 22
    return { x: sx(50 + rr * Math.cos(ang)), y: sy(50 + rr * Math.sin(ang)), n: i + 1, dist: it.distance_km }
  })
  return (
    <g>
      {[70, 110, 150].map((r, i) => <circle key={i} cx={cx} cy={cy} r={r} fill="none" stroke={`rgba(70,214,224,${0.07 - i * 0.02})`} strokeWidth={1} strokeDasharray="4,8" />)}
      {pts.map((p, i) => (
        <g key={i}>
          <line x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="rgba(70,214,224,0.12)" strokeWidth={1} strokeDasharray="3,5" />
          <circle cx={p.x} cy={p.y} r={14} fill="rgba(70,214,224,0.08)" stroke="rgba(70,214,224,0.25)" strokeWidth={1} style={{ animation: 'au-map-glow 3s ease-in-out infinite' }} />
          <circle cx={p.x} cy={p.y} r={8} fill="#46D6E0" />
          <text x={p.x} y={p.y + 3.5} fontSize={10} fill="#06080F" textAnchor="middle" fontFamily="var(--au-font-mono)" fontWeight={700}>{p.n}</text>
          {p.dist != null && <text x={p.x} y={p.y + 23} fontSize={9} fill="rgba(255,255,255,0.5)" textAnchor="middle" fontFamily="var(--au-font-mono)">{p.dist}km</text>}
        </g>
      ))}
      <circle cx={cx} cy={cy} r={10} fill="rgba(70,214,224,0.15)" stroke="#46D6E0" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={5} fill="#46D6E0" />
      <text x={cx} y={cy - 16} fontSize={10} fill="#46D6E0" textAnchor="middle">当前位置</text>
    </g>
  )
}

function PoiDetailView({ card }: { card: PoiDetailCard }) {
  const cx = sx(50), cy = sy(48)
  return (
    <g>
      {[60, 100].map((r, i) => <circle key={i} cx={cx} cy={cy} r={r} fill="none" stroke={`rgba(70,214,224,${0.08 - i * 0.03})`} strokeWidth={1} strokeDasharray="4,8" />)}
      <circle cx={cx} cy={cy} r={16} fill="rgba(70,214,224,0.12)" stroke="#46D6E0" strokeWidth={1.5} style={{ animation: 'au-map-glow 3s ease-in-out infinite' }} />
      <circle cx={cx} cy={cy} r={8} fill="#46D6E0" />
      <text x={cx} y={cy - 24} fontSize={13} fill="rgba(255,255,255,0.92)" textAnchor="middle" fontWeight={600}>{card.name}</text>
      {card.category && <text x={cx} y={cy + 32} fontSize={10} fill="rgba(255,255,255,0.5)" textAnchor="middle">{card.category}</text>}
    </g>
  )
}

// 路线：出发→途经(琥珀)→目的地(大) 折线流动虚线
function PathView({ card }: { card: RoutePlanCard }) {
  const labels = [card.origin || '当前位置', ...(card.waypoints || []).map((w) => w.name), card.destination]
  const nodes = labels.map((label, i, a) => {
    const t = a.length > 1 ? i / (a.length - 1) : 0
    const zig = i > 0 && i < a.length - 1 ? (i % 2 ? -5 : 5) : 0
    return { x: sx(22 + t * 56), y: sy(72 - t * 48 + zig), label, role: i === 0 ? 'origin' : i === a.length - 1 ? 'dest' : 'via' as const }
  })
  return (
    <g>
      <polyline points={nodes.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" stroke="#46D6E0" strokeWidth={3} strokeLinecap="round" strokeDasharray="12,6" style={{ animation: 'au-route-dash 2s linear infinite' }} />
      {nodes.map((p, i) => (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r={p.role === 'dest' ? 10 : 7} fill={p.role === 'via' ? '#F59E0B' : '#46D6E0'} stroke="rgba(6,8,15,0.8)" strokeWidth={2} />
          <text x={p.x} y={p.y - 14} fontSize={10} fill="rgba(255,255,255,0.7)" textAnchor="middle">{p.label}</text>
        </g>
      ))}
    </g>
  )
}

// 充电：出发→补电站(按 at_km 比例·琥珀⚡)→目的地 + 底部 SoC 条
function ChargeView({ card }: { card: ChargingRouteCard }) {
  const stops = card.stops || []
  const total = card.distance_km || (stops.length ? (stops[stops.length - 1].at_km || 0) * 1.25 : 100) || 100
  const ox = 8, dx = 92
  type N = { x: number; y: number; label: string; role: 'origin' | 'charge' | 'dest'; at?: number }
  const nodes: N[] = [
    { x: ox, y: 54, label: '出发', role: 'origin' },
    ...stops.map((s): N => ({ x: ox + (dx - ox) * Math.min((s.at_km || 0) / total, 0.9), y: 50, label: s.name, role: 'charge', at: s.at_km })),
    { x: dx, y: 46, label: card.destination, role: 'dest' },
  ]
  const soc = parseInt(card.soc || '', 10)
  return (
    <g>
      <polyline points={nodes.map((p) => `${sx(p.x)},${sy(p.y)}`).join(' ')} fill="none" stroke="#46D6E0" strokeWidth={2.5} strokeLinecap="round" strokeDasharray="10,5" style={{ animation: 'au-route-dash 3s linear infinite' }} />
      {nodes.map((p, i) => {
        const X = sx(p.x), Y = sy(p.y)
        if (p.role === 'charge') return (
          <g key={i}>
            <circle cx={X} cy={Y} r={13} fill="rgba(245,158,11,0.15)" stroke="#F59E0B" strokeWidth={1.5} />
            <text x={X} y={Y + 4} fontSize={12} textAnchor="middle">⚡</text>
            <text x={X} y={Y - 19} fontSize={9} fill="#F59E0B" textAnchor="middle">{p.label}</text>
            {p.at != null && <text x={X} y={Y + 24} fontSize={8.5} fill="rgba(255,255,255,0.45)" textAnchor="middle" fontFamily="var(--au-font-mono)">{p.at}km</text>}
          </g>
        )
        return (
          <g key={i}>
            <circle cx={X} cy={Y} r={8} fill={p.role === 'origin' ? '#46D6E0' : '#34D399'} stroke="rgba(6,8,15,0.8)" strokeWidth={2} />
            <text x={X} y={Y - 14} fontSize={9.5} fill="rgba(255,255,255,0.7)" textAnchor="middle">{p.label}</text>
          </g>
        )
      })}
      {Number.isFinite(soc) && (
        <g>
          <rect x={20} y={VB_H - 46} width={170} height={15} rx={7.5} fill="rgba(255,255,255,0.06)" stroke="rgba(255,255,255,0.10)" strokeWidth={1} />
          <rect x={22} y={VB_H - 44} width={Math.max(0, Math.min(100, soc)) / 100 * 166} height={11} rx={5.5} fill={soc > 50 ? '#46D6E0' : '#F59E0B'} opacity={0.85} />
          <text x={200} y={VB_H - 35} fontSize={10} fill={soc > 50 ? '#46D6E0' : '#F59E0B'} fontFamily="var(--au-font-mono)">{soc}% SoC</text>
        </g>
      )}
    </g>
  )
}

// 行程：按天连点 D1·D2·D3…（主题 + 停靠点数）
function ItineraryView({ card }: { card: TripItineraryCard }) {
  const days = card.itinerary || []
  const n = days.length || card.days || 1
  const nodes = days.map((d, i) => {
    const t = n > 1 ? i / (n - 1) : 0
    return { x: sx(18 + t * 60), y: sy(40 + (i % 2 ? 18 : -6) + i * 3), day: d.day_index ?? i + 1, theme: d.theme || `第${d.day_index ?? i + 1}天`, stops: d.stops?.length || 0 }
  })
  return (
    <g>
      {nodes.map((p, i) => i > 0 ? <line key={'l' + i} x1={nodes[i - 1].x} y1={nodes[i - 1].y} x2={p.x} y2={p.y} stroke="rgba(70,214,224,0.25)" strokeWidth={1.5} strokeDasharray="6,4" /> : null)}
      {nodes.map((p, i) => (
        <g key={'d' + i}>
          <circle cx={p.x} cy={p.y} r={13} fill="rgba(70,214,224,0.12)" stroke="rgba(255,255,255,0.3)" strokeWidth={1.5} />
          <text x={p.x} y={p.y + 4} fontSize={10} fill="rgba(255,255,255,0.9)" textAnchor="middle" fontFamily="var(--au-font-mono)" fontWeight={700}>D{p.day}</text>
          <text x={p.x} y={p.y - 19} fontSize={9.5} fill="rgba(255,255,255,0.6)" textAnchor="middle">{p.theme}</text>
          <text x={p.x} y={p.y + 25} fontSize={8.5} fill="rgba(255,255,255,0.4)" textAnchor="middle">{p.stops}个点</text>
        </g>
      ))}
    </g>
  )
}
