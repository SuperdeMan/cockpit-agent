import type { AgentInfo } from '../types'

function toPercent(value?: number): number | null {
  return value === undefined ? null : Math.round(value * 100)
}

export function AgentList({ agents }: { agents: Record<string, AgentInfo> }) {
  const ids = Object.keys(agents).sort()
  return (
    <section className="panel">
      <div className="panel__head">
        <div className="panel__title">
          <h2>Agent 运行状态</h2>
          <span className="en">Agents</span>
        </div>
        <span className="panel__tag">{ids.length} 个</span>
      </div>
      <div className="panel__body">
        {ids.length === 0 && <p className="empty">等待 agent 上报…</p>}
        <div className="agents">
          {ids.map((id) => {
            const agent = agents[id]
            const down = agent.healthy === false
            const errorPct = toPercent(agent.error_rate)
            return (
              <div
                key={id}
                data-agent={id}
                className={'arow' + (down ? ' arow--down' : '')}
              >
                <span className="arow__name">{id}</span>
                {agent.kind && (
                  <span
                    className={'kind' + (agent.kind.includes('edge') ? ' edge' : '')}
                  >
                    {agent.kind}
                  </span>
                )}
                <span className="arow__health">
                  <i />
                  {down ? '离线' : '健康'}
                </span>
                <span className="arow__metrics">
                  {agent.count !== undefined && (
                    <span>
                      <b>{agent.count}</b> 调用
                    </span>
                  )}
                  {agent.avg_ms !== undefined && (
                    <span>
                      <b>{agent.avg_ms}</b>ms
                    </span>
                  )}
                  {errorPct !== null && (
                    <span className={errorPct > 0 ? 'warn' : ''}>{errorPct}%</span>
                  )}
                  {down && agent.fail_count ? (
                    <span className="err">fail×{agent.fail_count}</span>
                  ) : null}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
