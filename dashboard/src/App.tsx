import { useEffect, useState, useSyncExternalStore } from 'react'
import { authorize, fetchHealth, fetchMeta, getAccessState, operatorToken, subscribeAccess } from './api'
import { useObservation } from './useObservation'
import { navigate, useRoute } from './navigation'
import { fixtureName, FIXTURE_TRACE, isFixture } from './fixtures'
import { FixtureGallery } from './FixtureGallery'
import { TopBar } from './components/shell/TopBar'
import { TokenGate } from './components/shell/TokenGate'
import { Banner } from './components/data'
import { Button } from './components/ui'
import { TurnsView } from './views/TurnsView'
import { LiveView } from './views/LiveView'
import { LogsView } from './views/LogsView'
import { LlmView } from './views/LlmView'
import type { CollectorMeta } from './types'
import './shell.css'

export default function App() {
  return fixtureName() === 'components' ? <FixtureGallery /> : <Observatory />
}
function Observatory() {
  const access = useSyncExternalStore(subscribeAccess, getAccessState)
  const route = useRoute()
  const [retry, setRetry] = useState(0)
  const [listOpen, setListOpen] = useState(false)
  const [meta, setMeta] = useState<CollectorMeta | null>(null)
  const [metaError, setMetaError] = useState('')
  const [nats, setNats] = useState<boolean>()
  const obs = useObservation(access === 'authenticated', retry)
  useEffect(() => {
    if (operatorToken() && !isFixture()) void authorize()
    else if (fixtureName()?.startsWith('token-') && fixtureName() !== 'token-first') void authorize()
    if (isFixture() && !new URLSearchParams(location.search).has('view')) {
      const name = fixtureName() || ''
      navigate({ view: name.startsWith('live') ? 'live' : name === 'logs' ? 'logs' : name === 'usage' ? 'llm' : 'turns',
        trace: ['turns', 'content-off', 'missing'].includes(name) ? FIXTURE_TRACE : '' }, true)
    }
  }, [])
  useEffect(() => {
    if (access !== 'authenticated') return
    let alive = true
    fetchMeta().then(value => { if (alive) { setMeta(value); setMetaError('') } }).catch(error => { if (alive) setMetaError(error.message) })
    fetchHealth().then(value => { if (alive) setNats(value.nats) }).catch(() => { if (alive) setNats(undefined) })
    return () => { alive = false }
  }, [access, retry])
  if (access !== 'authenticated') return <TokenGate state={access} />
  return <div className="observatory" data-list-open={listOpen}>
    <TopBar view={route.view} connected={obs.connected} nats={nats} onList={() => setListOpen(true)} />
    <div className="app-banners">
      {isFixture() && <div className="fixture-label">离线夹具 · 示例数据 · 所有操作仅在本页演示</div>}
      {!obs.connected && <Banner tone="warn" action={<Button size="sm" onClick={() => setRetry(n => n + 1)}>重连</Button>}>实时通道重连中 · 数据可能滞后</Banner>}
      {metaError && <Banner tone="warn" action={<Button size="sm" onClick={() => setRetry(n => n + 1)}>重试</Button>}>读取采集配置失败：{metaError}</Banner>}
      {meta?.content_capture === false && <Banner>内容未采集 · 原话、话术、规划与模型输入输出不可查看，链路形状仍可排查</Banner>}
    </div>
    <div className="app-content">
      {(route.view === 'turns' || route.view === 'badcases') && <TurnsView key={route.view} lastTurn={obs.lastTurn} traceId={route.trace} query={route.query} saved={route.view === 'badcases'} meta={meta} listOpen={listOpen} onCloseList={() => setListOpen(false)} />}
      {route.view === 'live' && <LiveView {...obs} meta={meta} />}
      {route.view === 'logs' && <LogsView lastLog={obs.lastLog} liveLogs={obs.liveLogs} />}
      {route.view === 'llm' && <LlmView lastTurn={obs.lastTurn} />}
    </div>
  </div>
}
