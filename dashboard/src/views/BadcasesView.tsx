// Badcase 收藏夹：标记列表（保留期豁免）→ 详情 → 重放对照。
// 重放复用 CommandBar 的 edge-gateway WS 通道：原话重发 + 新 trace，session 打 replay 标记。
import { useCallback, useEffect, useState } from 'react'

import { searchTurns } from '../api'
import { replayText } from '../components/CommandBar'
import { fmtTime, statusLabel, TurnDetailPanel } from '../components/TurnDetailPanel'
import type { Turn } from '../types'

export function BadcasesView({ turnTick }: { turnTick: number }) {
  const [items, setItems] = useState<Turn[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [replayOf, setReplayOf] = useState<string | null>(null) // 原 badcase trace
  const [replayTrace, setReplayTrace] = useState<string | null>(null) // 重放轮 trace
  const [replayState, setReplayState] = useState('')

  const load = useCallback(() => {
    searchTurns({ badcase: 1, limit: 100 })
      .then(setItems)
      .catch(() => setItems([]))
  }, [])

  useEffect(load, [load])
  // 新轮次事件到达时刷新（重放轮落库后对照面板自动出现内容）
  useEffect(() => {
    if (turnTick) load()
  }, [turnTick, load])

  const replay = (turn: Turn) => {
    if (!turn.user_text) return
    setReplayOf(turn.trace_id)
    setSelected(turn.trace_id)
    const traceId = replayText(turn.user_text, `replay-${turn.trace_id.slice(0, 8)}`, {
      onState: setReplayState,
    })
    setReplayTrace(traceId)
  }

  return (
    <div className="bad">
      <div className="sess-col sess-col--list panel">
        <div className="panel__head">
          <div className="panel__title">
            <h2>Badcase</h2>
            <span className="en">收藏夹</span>
          </div>
          <span className="panel__tag">{items.length}</span>
        </div>
        <div className="panel__body">
          {items.length === 0 && (
            <p className="empty">还没有标记——在轮次详情里点「☆ 标记 badcase」</p>
          )}
          {items.map((turn) => (
            <button
              key={turn.trace_id}
              className={'turn-row' + (turn.trace_id === selected ? ' turn-row--on' : '')}
              onClick={() => {
                setSelected(turn.trace_id)
                setReplayOf(null)
                setReplayTrace(null)
              }}
            >
              <div className="turn-row__user">
                <span>{turn.user_text || '（空）'}</span>
              </div>
              <div className="turn-row__reply">
                <span className={`det-status det-status--${turn.status}`}>
                  {statusLabel(turn.status)}
                </span>
                <span className="turn-row__speech">{turn.note || turn.speech || '—'}</span>
              </div>
              <div className="turn-row__meta">
                <i>{turn.session_id}</i>
                <i>{fmtTime(turn.ts)}</i>
                <i
                  className="det-btn"
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation()
                    replay(turn)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.stopPropagation()
                      replay(turn)
                    }
                  }}
                >
                  🔁 重放
                </i>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="sess-col sess-col--detail panel">
        <div className="panel__head">
          <div className="panel__title">
            <h2>{replayOf ? '原轮（badcase）' : '详情'}</h2>
            <span className="en">Turn Detail</span>
          </div>
        </div>
        <div className="panel__body">
          {!selected && <p className="empty">← 选择一条 badcase</p>}
          {selected && <TurnDetailPanel traceId={selected} onChanged={load} />}
        </div>
      </div>

      {replayOf && replayTrace && (
        <div className="sess-col sess-col--detail panel">
          <div className="panel__head">
            <div className="panel__title">
              <h2>重放轮（对照）</h2>
              <span className="en">Replay</span>
            </div>
            {replayState && <span className="panel__tag">{replayState}</span>}
          </div>
          <div className="panel__body">
            <TurnDetailPanel traceId={replayTrace} refreshKey={turnTick} />
          </div>
        </div>
      )}
    </div>
  )
}
