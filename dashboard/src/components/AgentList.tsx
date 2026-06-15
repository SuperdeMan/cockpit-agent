import type { AgentInfo } from '../types'

function healthLabel(agent: AgentInfo): string {
  if (agent.healthy === false) return '离线'
  if (agent.healthy === true) return '健康'
  return '待上报'
}

function lastSeen(value?: number): string {
  if (!value) return '--'
  const milliseconds = value < 10_000_000_000 ? value * 1000 : value
  return new Date(milliseconds).toLocaleTimeString('zh-CN', {
    hour12: false,
  })
}

export function AgentList({
  agents,
}: {
  agents: Record<string, AgentInfo>
}) {
  const ids = Object.keys(agents).sort((left, right) => {
    const healthDelta =
      Number(agents[left].healthy !== false) -
      Number(agents[right].healthy !== false)
    return healthDelta || left.localeCompare(right)
  })

  return (
    <section className="panel agent-panel" aria-labelledby="agent-panel-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SERVICE RUNTIME</p>
          <h2 id="agent-panel-title">Agent 运行状态</h2>
        </div>
        <span className="panel-count">{ids.length} agents</span>
      </div>

      {ids.length === 0 ? (
        <p className="agent-empty">等待 Registry 与 Cloud 指标上报</p>
      ) : (
        <div className="agent-list">
          {ids.map((id) => {
            const agent = agents[id]
            const state =
              agent.healthy === false
                ? 'down'
                : agent.healthy === true
                  ? 'healthy'
                  : 'unknown'
            return (
              <article
                key={id}
                className={`agent-row agent-row--${state}`}
                data-agent={id}
              >
                <span className="agent-row__signal" />
                <div className="agent-row__identity">
                  <strong>{id}</strong>
                  <span>
                    {agent.kind || 'agent'} / {agent.deployment || '--'}
                  </span>
                </div>
                <div className="agent-row__health">
                  <strong>{healthLabel(agent)}</strong>
                  <span>last {lastSeen(agent.last_seen)}</span>
                </div>
                <div className="agent-row__metrics">
                  <span>{agent.count ?? 0} calls</span>
                  <span>{agent.avg_ms ?? 0} ms</span>
                  <span>
                    {Math.round((agent.error_rate ?? 0) * 100)}% err
                  </span>
                  {agent.fail_count ? (
                    <span>{agent.fail_count} health fails</span>
                  ) : null}
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
