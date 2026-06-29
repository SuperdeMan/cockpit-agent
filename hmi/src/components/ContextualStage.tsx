// 右上下文舞台（P1 新增）——横屏带来的最大设计机会：随对话切换"场景"，让"此刻的车"在场。
// 场景由最近一条卡片/意图推导：天气卡→天气场景（呼应 A-2）；出行类卡→地图场景（呼应 A-5）；
// 否则回落待机场景（时钟 + 车辆概览 + 氛围）。媒体/车况场景待 HMI 侧补取数（P1 先占位）。
import { useEffect, useState } from 'react'
import { useSettings } from '../settings'
import { AuroraOrb } from './aurora'
import type { Msg, UiCard, WeatherCard, PoiListCard } from '../types'

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

// ── 天气场景（呼应 A-2 右舞台）：大温度 + 城市 + 天气文案 + 底部 telemetry ──
function WeatherStage({ card }: { card: WeatherCard }) {
  const today = card.forecast?.[0]
  const strip: Array<{ label: string; value: string }> = []
  if (today) strip.push({ label: '今日', value: `${today.temp_low}°–${today.temp_high}°` })
  if (card.precip) strip.push({ label: '降水', value: card.precip })
  if (card.air_quality) strip.push({ label: '空气', value: `${card.air_quality.category} ${card.air_quality.aqi}` })
  if (card.visibility) strip.push({ label: '能见度', value: card.visibility })
  if (!strip.length && card.humidity) strip.push({ label: '湿度', value: card.humidity })

  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '0 6%' }}>
      <div style={{ textAlign: 'right' }}>
        <div style={{ fontSize: 22, letterSpacing: '0.4em', color: 'var(--au-text-2)', paddingRight: '0.4em' }}>{card.city}</div>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end', marginTop: 6 }}>
          <span className="au-num" style={{ fontSize: 168, fontWeight: 700, letterSpacing: '-0.04em', lineHeight: 0.92 }}>{card.temp}</span>
          <span style={{ fontSize: 40, color: 'var(--au-text-2)', marginTop: 14 }}>°C</span>
        </div>
        <div style={{ fontSize: 30, fontWeight: 500, marginTop: 4 }}>{card.text}</div>
      </div>
      {strip.length > 0 && (
        <div className="au-glass" style={{ marginTop: 36, padding: '14px 8px', display: 'flex', justifyContent: 'space-around' }}>
          {strip.map((s) => (
            <div key={s.label} style={{ textAlign: 'center', padding: '0 10px' }}>
              <div className="au-num" style={{ fontSize: 17, fontWeight: 600 }}>{s.value}</div>
              <div style={{ fontSize: 11.5, color: 'var(--au-text-3)', marginTop: 3 }}>{s.label}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 地图场景（呼应 A-5 右舞台）：玻璃网格 + 编号 POI 标点 + 当前位置 ──
function MapStage({ card }: { card: UiCard }) {
  const isPoi = card.type === 'poi_list'
  const items = isPoi ? (card as PoiListCard).items.slice(0, 6) : []
  const title = isPoi
    ? (card as PoiListCard).title || (card as PoiListCard).keyword || '附近地点'
    : card.type === 'charging_route' ? '充电路线'
    : card.type === 'route_plan' ? '路线规划'
    : card.type === 'trip_itinerary' ? '行程地图'
    : '地点详情'

  // poi_list 无坐标 → 围绕"当前位置"按序确定性散布标点（数据驱动绘制区占位）
  const pin = (i: number, n: number) => {
    const ang = (-90 + (360 / Math.max(n, 1)) * i) * (Math.PI / 180)
    const r = 26 + (i % 2) * 8
    return { left: `${50 + r * Math.cos(ang)}%`, top: `${50 + r * Math.sin(ang)}%` }
  }

  return (
    <div style={{ position: 'absolute', inset: 0, backgroundImage: 'linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.05) 1px, transparent 1px)', backgroundSize: '56px 56px' }}>
      {/* 标题 chip */}
      <div className="au-glass" style={{ position: 'absolute', top: 18, left: 18, padding: '8px 16px', fontSize: 13.5, fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--au-primary)', boxShadow: '0 0 8px var(--au-primary)' }} />
        {title}
      </div>

      {/* 当前位置 */}
      <div style={{ position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
        <span style={{ width: 16, height: 16, borderRadius: '50%', background: 'var(--au-primary)', boxShadow: '0 0 0 5px rgba(70,214,224,0.18), 0 0 18px var(--au-primary)' }} />
        <span style={{ fontSize: 11, color: 'var(--au-text-2)' }}>当前位置</span>
      </div>

      {/* POI 标点（编号，呼应左侧卡「第N个」联动）*/}
      {items.map((it, i) => {
        const p = pin(i, items.length)
        return (
          <div key={it.id || i} style={{ position: 'absolute', left: p.left, top: p.top, transform: 'translate(-50%,-50%)', textAlign: 'center' }}>
            <span style={{ display: 'grid', placeItems: 'center', width: 24, height: 24, borderRadius: '50%', margin: '0 auto', fontSize: 12, fontWeight: 700, color: 'var(--au-primary-ink)', background: 'var(--au-primary)', boxShadow: '0 0 14px rgba(70,214,224,0.5)' }}>{i + 1}</span>
            {it.distance_km != null && <span className="au-num" style={{ fontSize: 10.5, color: 'var(--au-text-2)', marginTop: 2, display: 'block' }}>{it.distance_km}km</span>}
          </div>
        )
      })}

      {/* 非 POI 出行卡（路线/充电/行程）→ 简版占位提示 */}
      {!isPoi && (
        <div style={{ position: 'absolute', left: 0, right: 0, bottom: 24, textAlign: 'center', fontSize: 12.5, color: 'var(--au-text-3)' }}>
          路线与途经点见左侧卡片
        </div>
      )}

      <div style={{ position: 'absolute', right: 18, bottom: 16, fontSize: 12, color: 'var(--au-text-3)', letterSpacing: '0.04em' }}>地图占位 · 实现期接 SDK</div>
    </div>
  )
}
