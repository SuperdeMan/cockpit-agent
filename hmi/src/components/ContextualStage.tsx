// 右上下文舞台（P1 新增）——横屏带来的最大设计机会：随对话切换"场景"，让"此刻的车"在场。
// 场景由最近一条卡片/意图推导：天气卡→天气场景（呼应 A-2）；出行类卡→地图场景（呼应 A-5）；
// 否则回落待机场景（时钟 + 车辆概览 + 氛围）。媒体/车况场景待 HMI 侧补取数（P1 先占位）。
import { useEffect, useMemo, useState } from 'react'
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
