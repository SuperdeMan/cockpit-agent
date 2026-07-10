// 会话视图（badcase 排查主线）：会话列表 → 轮次时间线 → 轮次详情 三级下钻。
// 数据源 = collector SQLite REST；WS obs.turn 事件驱动实时刷新。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { fetchSessions, fetchSessionTurns, searchTurns } from '../api'
import { fmtTime, statusLabel, TurnDetailPanel } from '../components/TurnDetailPanel'
import type { SessionSummary, Turn } from '../types'

const TRACE_RE = /^[0-9a-f]{8,32}$/i

function SessionRow({
  session, active, onClick,
}: { session: SessionSummary; active: boolean; onClick: () => void }) {
  return (
    <button className={'sess-row' + (active ? ' sess-row--on' : '')} onClick={onClick}>
      <span className="sess-row__id">{session.session_id}</span>
      <span className="sess-row__meta">
        {session.turns} 轮
        {session.errors > 0 && <em className="sess-row__err">{session.errors} 错</em>}
        {session.rejected > 0 && <em className="sess-row__rej">{session.rejected} 拒</em>}
        {session.badcases > 0 && <em className="sess-row__bad">★{session.badcases}</em>}
      </span>
      <span className="sess-row__time">{fmtTime(session.last_ts)}</span>
    </button>
  )
}

function TurnRow({
  turn, active, onClick,
}: { turn: Turn; active: boolean; onClick: () => void }) {
  return (
    <button className={'turn-row' + (active ? ' turn-row--on' : '')} onClick={onClick}>
      <div className="turn-row__user">
        <span>{turn.user_text || '（空）'}</span>
        {!!turn.is_confirmation && <i className="det-chip">确认</i>}
      </div>
      <div className="turn-row__reply">
        <span className={`det-status det-status--${turn.status}`}>{statusLabel(turn.status)}</span>
        <span className="turn-row__speech">
          {turn.speech || (turn.ui_card_type ? `[${turn.ui_card_type}]` : '—')}
        </span>
      </div>
      <div className="turn-row__meta">
        {!!turn.badcase && <em className="sess-row__bad">★</em>}
        {turn.path && <i>{turn.path}</i>}
        {turn.ui_card_type && <i>{turn.ui_card_type}</i>}
        <i>{Math.round(turn.duration_ms)}ms</i>
        <i>{fmtTime(turn.ts)}</i>
      </div>
    </button>
  )
}

export function SessionsView({ lastTurn }: { lastTurn: Turn | null }) {
  const [query, setQuery] = useState('')
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [selectedTrace, setSelectedTrace] = useState<string | null>(null)
  const [detailRefresh, setDetailRefresh] = useState(0)
  const queryRef = useRef(query)
  queryRef.current = query

  const loadSessions = useCallback((q = queryRef.current) => {
    fetchSessions(q)
      .then(setSessions)
      .catch(() => setSessions([]))
  }, [])

  const loadTurns = useCallback((sessionId: string) => {
    fetchSessionTurns(sessionId)
      .then(setTurns)
      .catch(() => setTurns([]))
  }, [])

  useEffect(() => {
    loadSessions('')
  }, [loadSessions])

  // 搜索：trace_id 直达该轮；否则按文本过滤会话
  useEffect(() => {
    const handle = setTimeout(() => {
      const q = query.trim()
      if (TRACE_RE.test(q)) {
        searchTurns({ q, limit: 1 })
          .then((hits) => {
            if (hits.length) {
              setSelectedSession(hits[0].session_id)
              setSelectedTrace(hits[0].trace_id)
              loadTurns(hits[0].session_id)
            }
          })
          .catch(() => undefined)
        return
      }
      loadSessions(q)
    }, 250)
    return () => clearTimeout(handle)
  }, [query, loadSessions, loadTurns])

  // WS turn 事件实时刷新（列表 + 当前会话 + 当前轮详情）
  useEffect(() => {
    if (!lastTurn) return
    loadSessions()
    if (lastTurn.session_id === selectedSession) loadTurns(lastTurn.session_id)
    if (lastTurn.trace_id === selectedTrace) setDetailRefresh((n) => n + 1)
  }, [lastTurn, selectedSession, selectedTrace, loadSessions, loadTurns])

  const pickSession = (sessionId: string) => {
    setSelectedSession(sessionId)
    setSelectedTrace(null)
    loadTurns(sessionId)
  }

  const selectedTurns = useMemo(
    () => (selectedSession ? turns : []),
    [selectedSession, turns],
  )

  return (
    <div className="sess">
      <div className="sess-col sess-col--list panel">
        <div className="panel__head">
          <div className="panel__title">
            <h2>会话</h2>
            <span className="en">Sessions</span>
          </div>
          <span className="panel__tag">{sessions.length}</span>
        </div>
        <input
          className="sess-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜原话/话术，或粘 trace_id 直达"
        />
        <div className="panel__body">
          {sessions.length === 0 && (
            <p className="empty">还没有轮次数据——在 HMI 或指令台发一句话</p>
          )}
          {sessions.map((session) => (
            <SessionRow
              key={session.session_id}
              session={session}
              active={session.session_id === selectedSession}
              onClick={() => pickSession(session.session_id)}
            />
          ))}
        </div>
      </div>

      <div className="sess-col sess-col--turns panel">
        <div className="panel__head">
          <div className="panel__title">
            <h2>轮次</h2>
            <span className="en">Turns</span>
          </div>
          {selectedSession && <span className="panel__tag">{selectedSession}</span>}
        </div>
        <div className="panel__body">
          {!selectedSession && <p className="empty">← 选择一个会话</p>}
          {selectedSession && selectedTurns.length === 0 && (
            <p className="empty">该会话暂无轮次</p>
          )}
          {selectedTurns.map((turn) => (
            <TurnRow
              key={turn.trace_id}
              turn={turn}
              active={turn.trace_id === selectedTrace}
              onClick={() => setSelectedTrace(turn.trace_id)}
            />
          ))}
        </div>
      </div>

      <div className="sess-col sess-col--detail panel">
        <div className="panel__head">
          <div className="panel__title">
            <h2>详情</h2>
            <span className="en">Turn Detail</span>
          </div>
          <span className="panel__tag">怎么错的</span>
        </div>
        <div className="panel__body">
          {!selectedTrace && <p className="empty">← 选择一轮查看全链路</p>}
          {selectedTrace && (
            <TurnDetailPanel
              traceId={selectedTrace}
              refreshKey={detailRefresh}
              onChanged={() => {
                loadSessions()
                if (selectedSession) loadTurns(selectedSession)
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}
