// A-8 图标库验证台（?icons）：全量图标网格 + 4 状态 + 推荐尺寸，核对落地与 Figma 一致。
import { Icon, ICON_NAMES, ICON_SIZES, type IconState } from '../Icon'

const STATES: IconState[] = ['default', 'active', 'disabled', 'aiMoment']

export function IconGallery() {
  return (
    <div style={{ minHeight: '100vh', background: 'var(--au-bg)', color: 'var(--au-text)', fontFamily: 'var(--au-font-ui)', padding: 40 }}>
      <h1 className="au-aurora-text" style={{ fontSize: 24, fontWeight: 700, margin: '0 0 4px' }}>A-8 Icon Library · 落地验证</h1>
      <div style={{ fontSize: 13, color: 'var(--au-text-3)', marginBottom: 28 }}>{ICON_NAMES.length} 个图标 · 24×24 / 1.8 描边 / 圆角 · 状态 default·active·disabled·aiMoment</div>

      {/* 全量网格 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(120px,1fr))', gap: 12, marginBottom: 40 }}>
        {ICON_NAMES.map((n) => (
          <div key={n} className="au-glass" style={{ padding: '14px 8px', borderRadius: 14, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <Icon name={n} size={26} state="default" />
            <span style={{ fontSize: 10, color: 'var(--au-text-3)', textAlign: 'center', wordBreak: 'break-all' }}>{n}</span>
          </div>
        ))}
      </div>

      {/* 4 状态 */}
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 14px' }}>状态规范</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: 40 }}>
        {STATES.map((st) => (
          <div key={st} style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
            <span style={{ width: 80, fontSize: 12, color: st === 'active' ? 'var(--au-primary)' : 'var(--au-text-2)' }}>{st}</span>
            {(['assistant', 'voice-input', 'weather-cloudy', 'charging-station', 'warning', 'memory', 'vehicle', 'dining'] as const).map((n) => (
              <Icon key={n} name={n} size={26} state={st} />
            ))}
          </div>
        ))}
      </div>

      {/* 尺寸 */}
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: '0 0 14px' }}>推荐尺寸</h2>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 40 }}>
        {ICON_SIZES.map((s) => (
          <div key={s} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <Icon name="assistant" size={s} state="active" />
            <span className="au-num" style={{ fontSize: 11, color: 'var(--au-text-3)' }}>{s}px</span>
          </div>
        ))}
      </div>
    </div>
  )
}
