// AQI 7 档色阶（设计契约 §3-A）：优/良/轻度/中度/重度/严重 + 未知。当前档高亮发光。
// 数字用等宽（au-num）；不以颜色为唯一信息载体（带档位文字）。
const LEVELS = [
  { name: '优', range: '0–50', v: '--au-aqi-1', max: 50 },
  { name: '良', range: '51–100', v: '--au-aqi-2', max: 100 },
  { name: '轻度', range: '101–150', v: '--au-aqi-3', max: 150 },
  { name: '中度', range: '151–200', v: '--au-aqi-4', max: 200 },
  { name: '重度', range: '201–300', v: '--au-aqi-5', max: 300 },
  { name: '严重', range: '301+', v: '--au-aqi-6', max: Infinity },
]

export function AQISection({ aqi, category }: { aqi?: number | string; category?: string }) {
  const n = typeof aqi === 'string' ? parseInt(aqi, 10) : aqi
  const idx = n == null || Number.isNaN(n) ? -1 : LEVELS.findIndex((l) => (n as number) <= l.max)
  const active = idx >= 0 ? LEVELS[idx] : null
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 13, color: 'var(--au-text-2)' }}>空气质量</span>
        {active && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--au-text)' }}>
            <i style={{ width: 7, height: 7, borderRadius: '50%', background: `var(${active.v})`, flex: 'none' }} />
            <b className="au-num" style={{ color: `var(${active.v})` }}>{n}</b> {category || active.name}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', gap: 3 }}>
        {LEVELS.map((l, i) => (
          <div
            key={l.name}
            title={`${l.name} ${l.range}`}
            style={{
              flex: 1,
              height: 6,
              borderRadius: 3,
              background: `var(${l.v})`,
              opacity: idx < 0 ? 0.5 : i === idx ? 1 : 0.28,
              boxShadow: i === idx ? `0 0 10px var(${l.v})` : 'none',
            }}
          />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 3, marginTop: 4 }}>
        {LEVELS.map((l) => (
          <span key={l.name} style={{ flex: 1, textAlign: 'center', fontSize: 10, color: 'var(--au-text-3)' }}>
            {l.name}
          </span>
        ))}
      </div>
    </div>
  )
}
