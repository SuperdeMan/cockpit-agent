// 轮次详情（badcase 排查的"一屏全貌"）：内容检查器 + span 瀑布 + LLM 调用 + 关联日志 + 标记/导出。
import { useEffect, useState } from 'react'

import { fetchExport, fetchTurnDetail, markBadcase } from '../api'
import type { LlmCall, LogEntry, TurnDetail } from '../types'
import { SpanWaterfall } from './SpanWaterfall'

const STATUS_LABEL: Record<string, string> = {
  ok: '成功', err: '失败', rejected: '拒识', clarify: '澄清',
  need_confirm: '待确认', cancelled: '已打断', empty: '空响应', timeout: '超时',
}

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] || status || '—'
}

export function fmtTime(ts?: number): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}:${ss}`
}

function LlmRow({ call }: { call: LlmCall }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="det-llm">
      <button className="det-llm__head" onClick={() => setOpen(!open)}>
        <span className="det-llm__caller">{call.caller || '—'}</span>
        <span className="det-llm__model">{call.model}</span>
        <span className="det-llm__tok">
          {call.prompt_tokens}+{call.completion_tokens} tok
        </span>
        <span className="det-llm__ms">{Math.round(call.latency_ms)}ms</span>
        {!!call.cache_hit && <span className="det-chip">cache</span>}
        {!!call.thinking && <span className="det-chip">思考</span>}
        <span className={`trace-node__st st-${call.status || 'ok'}`}>
          {call.status || 'ok'}
        </span>
      </button>
      {open && (
        <div className="det-llm__body">
          {call.prompt_tail && (
            <div>
              <p className="det-label">prompt 末段</p>
              <pre>{call.prompt_tail}</pre>
            </div>
          )}
          {call.content_head && (
            <div>
              <p className="det-label">输出头部</p>
              <pre>{call.content_head}</pre>
            </div>
          )}
          {call.error && <pre className="det-err">{call.error}</pre>}
        </div>
      )}
    </div>
  )
}

function LogLine({ log }: { log: LogEntry }) {
  return (
    <div className={`det-log det-log--${(log.level || '').toLowerCase()}`}>
      <span className="det-log__ts">{fmtTime(log.ts)}</span>
      <span className="det-log__svc">{log.service}</span>
      <span className="det-log__lvl">{log.level}</span>
      <span className="det-log__msg">{log.msg}</span>
    </div>
  )
}

export function TurnDetailPanel({
  traceId,
  refreshKey = 0,
  onChanged,
}: {
  traceId: string
  refreshKey?: number
  onChanged?: () => void
}) {
  const [detail, setDetail] = useState<TurnDetail | null>(null)
  const [missing, setMissing] = useState(false)
  const [note, setNote] = useState('')
  const [copied, setCopied] = useState<'trace' | 'json' | null>(null)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setMissing(false)
    fetchTurnDetail(traceId)
      .then((d) => {
        if (cancelled) return
        if (!d || 'error' in d) setMissing(true)
        else {
          setDetail(d)
          setNote(d.turn?.note || '')
        }
      })
      .catch(() => !cancelled && setMissing(true))
    return () => {
      cancelled = true
    }
  }, [traceId, refreshKey])

  if (missing) return <p className="empty">没找到这轮（trace_id: {traceId}）</p>
  if (!detail) return <p className="empty">加载中…</p>

  const turn = detail.turn
  const planning = detail.spans.find((s) => s.node === 'cloud.planning')
  const plan = planning?.attrs?.plan as string | undefined
  const llmRaw = planning?.attrs?.llm_raw as string | undefined

  const copyTrace = () => {
    void navigator.clipboard?.writeText(traceId).catch(() => undefined)
    setCopied('trace')
    setTimeout(() => setCopied(null), 1200)
  }
  const copyJson = async () => {
    try {
      const data = await fetchExport(traceId)
      await navigator.clipboard?.writeText(JSON.stringify(data, null, 2))
      setCopied('json')
      setTimeout(() => setCopied(null), 1200)
    } catch {
      /* 导出失败静默（面板仍可手动打开 API） */
    }
  }
  const toggleBadcase = async () => {
    if (!turn) return
    const next = !turn.badcase
    const ok = await markBadcase(traceId, next, note)
    if (ok) {
      setDetail({ ...detail, turn: { ...turn, badcase: next ? 1 : 0, note } })
      onChanged?.()
    }
  }
  const saveNote = async () => {
    if (!turn || !turn.badcase) return
    const ok = await markBadcase(traceId, true, note)
    if (ok) {
      setDetail({ ...detail, turn: { ...turn, note } })
      onChanged?.()
    }
  }

  return (
    <div className="det">
      <div className="det-head">
        <button className="det-trace" onClick={copyTrace} title="复制完整 trace_id">
          {copied === 'trace' ? '已复制' : `#${traceId.slice(0, 12)}`}
        </button>
        {turn && (
          <>
            <span className={`det-status det-status--${turn.status}`}>
              {statusLabel(turn.status)}
            </span>
            {turn.path && <span className="det-chip">{turn.path}</span>}
            {!!turn.is_confirmation && <span className="det-chip">确认轮</span>}
            <span className="det-ms">{Math.round(turn.duration_ms)}ms</span>
            <span className="det-time">{fmtTime(turn.ts)}</span>
          </>
        )}
        <span className="det-actions">
          <button className="det-btn" onClick={copyJson}>
            {copied === 'json' ? '已复制' : '导出 JSON'}
          </button>
          <button
            className={'det-btn' + (turn?.badcase ? ' det-btn--on' : '')}
            onClick={toggleBadcase}
            disabled={!turn}
          >
            {turn?.badcase ? '★ 已标记 badcase' : '☆ 标记 badcase'}
          </button>
        </span>
      </div>

      {!!turn?.badcase && (
        <div className="det-note">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={saveNote}
            onKeyDown={(e) => e.key === 'Enter' && saveNote()}
            placeholder="备注：哪里不对？（回车/失焦保存）"
          />
        </div>
      )}

      {turn && (
        <section className="det-sec">
          <p className="det-label">
            用户说{turn.input_source ? `（${turn.input_source}）` : ''}
          </p>
          <div className="det-usertext">{turn.user_text || '—'}</div>
          <p className="det-label">系统答{turn.ui_card_type ? ` · 卡片 ${turn.ui_card_type}` : ''}{turn.actions ? ` · ${turn.actions} 个动作` : ''}</p>
          <div className="det-speech">{turn.speech || '（无话术）'}</div>
          {turn.error && <pre className="det-err">{turn.error}</pre>}
        </section>
      )}

      {(plan || llmRaw) && (
        <section className="det-sec">
          <p className="det-label">Planner 产出（cloud.planning）</p>
          {plan && <pre className="det-pre">{plan}</pre>}
          {llmRaw && (
            <details className="det-details">
              <summary>LLM 原始输出</summary>
              <pre className="det-pre">{llmRaw}</pre>
            </details>
          )}
        </section>
      )}

      <section className="det-sec">
        <p className="det-label">链路（{detail.spans.length} span）</p>
        <SpanWaterfall spans={detail.spans} />
      </section>

      {detail.llm_calls.length > 0 && (
        <section className="det-sec">
          <p className="det-label">LLM 调用（{detail.llm_calls.length} 次）</p>
          {detail.llm_calls.map((call, index) => (
            <LlmRow key={call.id ?? index} call={call} />
          ))}
        </section>
      )}

      <section className="det-sec">
        <p className="det-label">关联日志（{detail.logs.length} 条）</p>
        {detail.logs.length === 0 && (
          <p className="empty">这轮没有带 trace 的日志（P1 日志贯通后此处按 trace 聚合）</p>
        )}
        {detail.logs.map((log, index) => (
          <LogLine key={log.id ?? index} log={log} />
        ))}
      </section>
    </div>
  )
}
