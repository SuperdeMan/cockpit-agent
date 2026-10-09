import { useEffect, useMemo, useState } from 'react'
import { CardRenderer } from './Cards'
import { cardFixtures } from '../cardFixtures.mjs'
export function CardGallery() {
  const query = new URLSearchParams(window.location.search)
  const fixtures = useMemo(cardFixtures, [])
  const index = Math.min(fixtures.length - 1, Math.max(0, Number(query.get('card-gallery')) || 0))
  const [sent, setSent] = useState('')
  const fixture = fixtures[index]
  useEffect(() => {
    document.documentElement.dataset.theme = query.get('theme') === 'light' ? 'light' : 'dark'
    document.documentElement.dataset.drive = query.get('drive') === 'on' ? 'on' : 'off'
    document.documentElement.dataset.font = query.get('font') === 'large' ? 'large' : 'normal'
  }, [])
  return <main className="au-card-gallery" data-fixture-count={fixtures.length} data-fixture-index={index}>
    <header><strong>卡片视觉夹具 · 示例数据</strong><p>只验证前端呈现，不代表真实业务读数。{index + 1} / {fixtures.length} · {fixture.label.replace(/真栈已验/g, '样例')}</p></header>
    <div className="au-gallery-preview"><CardRenderer card={fixture.card} driving={query.get('drive') === 'on'} onAction={setSent} /></div>
    {sent && <output>普通上行语句：{sent}</output>}
  </main>
}
