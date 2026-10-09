import { useEffect, useState } from 'react'
import { isFixture, observationNow } from '../fixtures'
import type { AgentInfo } from '../types'
import { Duration, EmptyState, Tag } from './data'
import { Button } from './ui'

type AgentState = 'healthy' | 'offline' | 'circuit-open' | 'half-open' | 'degraded' | 'no-metrics'
function stateOf(agent: AgentInfo): AgentState {
  if (agent.healthy === false) return 'offline'
  if (agent.circuit === 'open') return 'circuit-open'
  if (agent.circuit === 'half_open' || agent.circuit === 'half-open') return 'half-open'
  if (agent.degrade && agent.degrade > 0) return 'degraded'
  return !agent.count ? 'no-metrics' : 'healthy'
}
const ORDER: Record<AgentState, number> = { offline: 0, 'circuit-open': 1, 'half-open': 2, degraded: 3, healthy: 4, 'no-metrics': 5 }
const LABEL: Record<AgentState, string> = { healthy: '健康', offline: '离线', 'circuit-open': '熔断中', 'half-open': '半开探测', degraded: '降级', 'no-metrics': '启动后无调用' }

export function AgentTile({ id, agent, now }: { id: string; agent: AgentInfo; now: number }) {
  const state = stateOf(agent)
  const tone = state === 'offline' || state === 'circuit-open' ? 'critical' : state === 'half-open' || state === 'degraded' ? 'warn' : 'neutral'
  const age = agent.last_seen === undefined ? null : Math.max(0, Math.floor((now - agent.last_seen) / 1000))
  const label = state === 'healthy' && agent.healthy === undefined ? '健康未上报' : LABEL[state]
  return <div className="agent-tile" data-agent={id} data-state={state}>
    <div className="row"><strong className="grow">{id}</strong>{agent.kind && <span className="mono muted">{agent.kind}</span>}</div>
    <div className="agent-tile__metrics"><Tag tone={tone}>{label}</Tag>{agent.count !== undefined && agent.count > 0 && <><span>{agent.count} 次</span><Duration ms={agent.avg_ms} /><span>{agent.error_rate === undefined ? '错误率 —' : (agent.error_rate * 100).toFixed(1) + '%'}</span></>}
      <span className="agent-tile__meta">{age === null ? '未上报时间' : age < 60 ? age + ' 秒前' : Math.floor(age / 60) + ' 分钟前'}</span>{agent.degrade !== undefined && <span>降级 {agent.degrade}</span>}{agent.llm_tokens !== undefined && <span>{agent.llm_tokens} tokens</span>}</div>
  </div>
}
export function AgentList({ agents }: { agents: Record<string, AgentInfo> }) {
  const [all, setAll] = useState(false)
  const [now, setNow] = useState(observationNow)
  useEffect(() => { if (isFixture()) return; const timer = setInterval(() => setNow(Date.now()), 10000); return () => clearInterval(timer) }, [])
  const ids = Object.keys(agents).sort((a, b) => ORDER[stateOf(agents[a])] - ORDER[stateOf(agents[b])] || a.localeCompare(b))
  const healthy = ids.length > 0 && ids.every(id => agents[id].healthy === true)
  return <section className="panel agent-panel"><div className="panel__head"><div className="row wrap"><h2>Agent</h2><span className="caption">{ids.length} 个{healthy ? ' · 全部健康' : ''}</span></div>{ids.length > 6 && <Button size="sm" kind="ghost" onClick={() => setAll(!all)}>{all ? '收起' : '查看全部 ' + ids.length}</Button>}</div>
    <div className="panel__body">{!ids.length ? <EmptyState title="等待 Agent 上报" /> : <div className="agent-grid">{ids.slice(0, all ? undefined : 6).map(id => <AgentTile key={id} id={id} agent={agents[id]} now={now} />)}</div>}</div>
  </section>
}
