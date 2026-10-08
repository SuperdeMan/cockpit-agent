// I1 fixture: the Figma typography specimen rendered with runtime CSS variables.
// Open ?tokens[&theme=light][&drive=on][&font=large]. No service or hardware access.
import { useEffect } from 'react'

const samples = [
  ['display', '下午好'], ['headline', '雷电黄色预警'], ['title', '附近充电站'],
  ['answer', '找到 4 个充电站，最近的 1.2 公里'], ['body', '特来电 · 西湖文化广场站'],
  ['body', '星星充电 · 黄龙站', 'strong'], ['label', '导航去第 1 个'], ['caption', '和风天气 · 17:30'],
  ['num-hero', '28°'], ['num-xl', '412'], ['num-l', '78%'], ['num-m', '1.2 km'],
]
export function VisualTokensPreview() {
  const query = new URLSearchParams(window.location.search)
  const driving = query.get('drive') === 'on'
  const large = query.get('font') === 'large'
  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = query.get('theme') === 'light' ? 'light' : 'dark'
    root.dataset.drive = driving ? 'on' : 'off'
    root.dataset.font = large ? 'large' : 'normal'
    const css = getComputedStyle(root)
    document.querySelectorAll<HTMLElement>('[data-token-role]').forEach(label => {
      const role = label.dataset.tokenRole!
      label.textContent = `${label.dataset.tokenLabel} · ${css.getPropertyValue(`--au-type-${role}-size`).trim().replace('px', '')} / ${css.getPropertyValue(`--au-type-${role}-line`).trim().replace('px', '')}`
    })
  }, [driving, large])
  return <main style={{ background: 'var(--au-bg)', color: 'var(--au-text)', fontFamily: 'var(--au-font-ui)', minHeight: '100vh', padding: 64 }}>
    <section data-testid="token-specimen" style={{ width: 820, padding: 40, display: 'flex', flexDirection: 'column', gap: 28,
      border: '1px solid var(--au-line)', borderRadius: 'var(--au-r-2xl)', background: 'var(--au-surface-1)' }}>
      <h1 className="au-type-title" style={{ margin: 0 }}>{driving ? '行车' : '泊车'}·{large ? '大字' : '标准'}</h1>
      {samples.map(([role, value, strong], index) => <div key={index} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div data-token-role={role} data-token-label={`${role.replace(/-/g, '/')}${strong ? '/strong' : ''}`}
          className="au-type-caption" style={{ color: 'var(--au-text-2)' }}>{role}{strong ? '/strong' : ''}</div>
        <div data-type={role} className={`au-type-${role}${role.startsWith('num') ? ' au-num' : ''}`}
          style={{ fontWeight: strong ? 500 : ['display', 'headline', 'title', 'num-xl', 'num-l'].includes(role) ? 700 : role.startsWith('num') || role === 'label' ? 500 : 400 }}>
          {value}
        </div>
      </div>)}
    </section>
  </main>
}
