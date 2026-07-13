// LLM 视图：消耗归属汇总（caller_service × model 分组）。
// 2026-07-13 消耗排查的收尾一环——「谁在花 token」不再翻库：时间窗内每个调用方的
// 次数/输入输出 tokens/错误/时延一屏可见；「(未归属)」行高亮 = 直连网关未带
// caller_service 的盲区（按 conventions §9.2 应恒为零，出现即待修）。
import { useCallback, useEffect, useState } from 'react'

import { fetchLlmSummary } from '../api'
import { fmtTime } from '../components/TurnDetailPanel'
import type { LlmSummaryGroup, Turn } from '../types'

const WINDOWS: ReadonlyArray<readonly [number, string]> = [
  [1, '1 小时'],
  [24, '24 小时'],
  [24 * 7, '7 天'],
  [24 * 30, '30 天'],
]

export function fmtTokens(n: number): string {
  if (!Number.isFinite(n)) return '0'
  if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + '万'
  return n.toLocaleString()
}

export function LlmView({ lastTurn }: { lastTurn: Turn | null }) {
  const [hours, setHours] = useState(24)
  const [groups, setGroups] = useState<LlmSummaryGroup[]>([])

  const load = useCallback((h: number) => {
    fetchLlmSummary(h)
      .then((s) => setGroups(s.groups))
      .catch(() => setGroups([]))
  }, [])

  useEffect(() => {
    load(hours)
  }, [hours, load])

  // 每个新轮次都可能带 LLM 调用 → 跟随 turn 事件刷新（与会话视图同款驱动）
  useEffect(() => {
    if (lastTurn) load(hours)
  }, [lastTurn, hours, load])

  const totals = groups.reduce(
    (acc, g) => {
      acc.calls += g.calls
      acc.tokens += g.prompt_tokens + g.completion_tokens
      acc.errors += g.errors
      if (g.caller === '(未归属)') acc.blind += g.calls
      return acc
    },
    { calls: 0, tokens: 0, errors: 0, blind: 0 },
  )

  return (
    <div className="llm panel grow">
      <div className="panel__head">
        <div className="panel__title">
          <h2>LLM 消耗归属</h2>
          <span className="en">Token Attribution</span>
        </div>
        <div className="llm-windows">
          {WINDOWS.map(([h, label]) => (
            <button
              key={h}
              className={'llm-win' + (hours === h ? ' llm-win--on' : '')}
              onClick={() => setHours(h)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="llm-tiles">
        <div className="llm-tile">
          <b>{fmtTokens(totals.calls)}</b>
          <span>调用次数</span>
        </div>
        <div className="llm-tile">
          <b>{fmtTokens(totals.tokens)}</b>
          <span>总 tokens（入+出）</span>
        </div>
        <div className={'llm-tile' + (totals.errors ? ' llm-tile--warn' : '')}>
          <b>{totals.errors}</b>
          <span>错误</span>
        </div>
        <div className={'llm-tile' + (totals.blind ? ' llm-tile--warn' : '')}>
          <b>{totals.blind}</b>
          <span>未归属调用（应为 0）</span>
        </div>
      </div>

      <div className="panel__body">
        {groups.length === 0 && <p className="empty">该时间窗内没有 LLM 调用</p>}
        {groups.length > 0 && (
          <table className="llm-table">
            <thead>
              <tr>
                <th>调用方</th>
                <th>模型</th>
                <th className="num">次数</th>
                <th className="num">输入 tokens</th>
                <th className="num">输出 tokens</th>
                <th className="num">错误</th>
                <th className="num">均时延</th>
                <th>最近调用</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr
                  key={g.caller + '|' + g.model}
                  className={g.caller === '(未归属)' ? 'llm-row--blind' : ''}
                >
                  <td>{g.caller}</td>
                  <td className="mono">{g.model}</td>
                  <td className="num">{g.calls}</td>
                  <td className="num">{fmtTokens(g.prompt_tokens)}</td>
                  <td className="num">{fmtTokens(g.completion_tokens)}</td>
                  <td className={'num' + (g.errors ? ' llm-err' : '')}>{g.errors}</td>
                  <td className="num">{Math.round(g.avg_latency_ms)}ms</td>
                  <td className="mono">{fmtTime(g.last_ts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
