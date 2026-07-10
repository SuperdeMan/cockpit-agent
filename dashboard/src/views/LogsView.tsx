// 日志视图：全局结构化日志检索 + WS 实时追加（P1 obs.log 贯通后有数据；此前空态提示）。
import { useEffect, useRef, useState } from 'react'

import { fetchLogs } from '../api'
import { fmtTime } from '../components/TurnDetailPanel'
import type { LogEntry } from '../types'

const LEVELS = ['', 'WARNING', 'ERROR', 'INFO'] as const

export function LogsView({ lastLog }: { lastLog: LogEntry | null }) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [service, setService] = useState('')
  const [level, setLevel] = useState('')
  const [query, setQuery] = useState('')
  const [follow, setFollow] = useState(true)
  const listRef = useRef<HTMLDivElement | null>(null)
  const filtersRef = useRef({ service, level, query })
  filtersRef.current = { service, level, query }

  const load = () => {
    fetchLogs({
      service: filtersRef.current.service,
      level: filtersRef.current.level,
      q: filtersRef.current.query,
      limit: 300,
    })
      .then(setLogs)
      .catch(() => setLogs([]))
  }

  useEffect(() => {
    const handle = setTimeout(load, 200)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [service, level, query])

  // WS 实时追加（按当前过滤条件放行）
  useEffect(() => {
    if (!lastLog) return
    const f = filtersRef.current
    if (f.service && lastLog.service !== f.service) return
    if (f.level && lastLog.level !== f.level.toUpperCase()) return
    if (f.query && !(lastLog.msg || '').includes(f.query)) return
    setLogs((previous) => [...previous.slice(-499), lastLog])
  }, [lastLog])

  useEffect(() => {
    if (follow && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [logs, follow])

  const services = Array.from(new Set(logs.map((l) => l.service).filter(Boolean)))

  return (
    <div className="logs panel">
      <div className="panel__head">
        <div className="panel__title">
          <h2>日志</h2>
          <span className="en">Structured Logs</span>
        </div>
        <span className="panel__tag">{logs.length}</span>
      </div>
      <div className="logs-bar">
        <select value={service} onChange={(e) => setService(e.target.value)}>
          <option value="">全部服务</option>
          {services.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)}>
          {LEVELS.map((l) => (
            <option key={l} value={l}>{l || '全部级别'}</option>
          ))}
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="按内容过滤…"
        />
        <label className="logs-follow">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
          />
          跟随
        </label>
      </div>
      <div className="panel__body" ref={listRef}>
        {logs.length === 0 && (
          <p className="empty">
            暂无日志——服务经 obs.log 上报（WARNING+ 与带 trace 的 INFO）
          </p>
        )}
        {logs.map((log, index) => (
          <div
            key={log.id ?? `${log.ts}-${index}`}
            className={`det-log det-log--${(log.level || '').toLowerCase()}`}
          >
            <span className="det-log__ts">{fmtTime(log.ts)}</span>
            <span className="det-log__svc">{log.service}</span>
            <span className="det-log__lvl">{log.level}</span>
            <span className="det-log__msg">{log.msg}</span>
            {log.trace_id && <span className="det-log__trace">#{log.trace_id.slice(0, 8)}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}
