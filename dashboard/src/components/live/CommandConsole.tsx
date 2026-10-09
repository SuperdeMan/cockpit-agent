import { useEffect, useRef, useState } from 'react'
import { fixtureName, fixtureTurns, isFixture } from '../../fixtures'
import { navigate } from '../../navigation'
import type { StateChange } from '../../types'
import { EDGE_WS_URL, genTraceId } from '../CommandBar'
import { Button, FilterChip, Input } from '../ui'
import { Tag } from '../data'

export type CommandState = 'idle' | 'connecting' | 'running' | 'done' | 'failed' | 'unreachable'
type CommandReason = '' | 'gateway-error' | 'connect-error' | 'connect-timeout' | 'send-error' | 'result-timeout' | 'connection-lost' | 'transport-error' | 'invalid-response'
const UNKNOWN_RESULT = new Set<CommandReason>(['result-timeout', 'connection-lost', 'transport-error', 'invalid-response'])
const STATES: Record<CommandState, string> = { idle: '空闲', connecting: '连接中', running: '执行中', done: '完成', failed: '失败', unreachable: '连不上网关' }
const QUICK = ['空调调到26度', '打开主驾座椅加热', '氛围灯调成绿色', '导航去机场顺便订今晚的餐', '打开后备箱']
export function fixtureCommandState(): CommandState {
  const name = fixtureName()
  return name === 'live-running' ? 'running' : name === 'live-connecting' ? 'connecting' : name === 'live-failed' ? 'failed' : name === 'live-unreachable' ? 'unreachable' : name === 'live-done' || name === 'live-pending' ? 'done' : 'idle'
}
export function fixtureCommandTrace(): string {
  const name = fixtureName()
  return name === 'live-pending' ? fixtureTurns[2].trace_id : name === 'live-done' || name === 'live-running' ? fixtureTurns[12].trace_id : ''
}

export function CommandConsole({ onTrace, onState, changes, hasEvidence = false }: {
  onTrace?: (traceId: string, text: string) => void; onState?: (state: CommandState) => void; changes?: StateChange[]; hasEvidence?: boolean
}) {
  const [text, setText] = useState('')
  const [state, setState] = useState<CommandState>(fixtureCommandState)
  const [reason, setReason] = useState<CommandReason>('')
  const [traceId, setTraceId] = useState(fixtureCommandTrace)
  const [reply, setReply] = useState(() => state === 'done' ? '离线示例：' + (fixtureTurns.find(turn => turn.trace_id === fixtureCommandTrace())?.speech || '请求已取得最终结果。') : state === 'failed' ? '网关返回了错误。' : state === 'unreachable' ? '无法建立网关连接。' : '')
  const socket = useRef<WebSocket | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const close = () => { clearTimeout(timer.current); const previous = socket.current; socket.current = null; if (previous) { previous.onopen = null; previous.onmessage = null; previous.onclose = null; previous.onerror = null; previous.close() } }
  useEffect(() => close, [])
  useEffect(() => { onState?.(state) }, [state])
  const send = (override?: string) => {
    const command = (override ?? text).trim()
    if (!command || state === 'connecting' || state === 'running') return
    close(); setText(command); setReply(''); setReason(''); setState('connecting')
    const nextTrace = isFixture() ? fixtureCommandTrace() || fixtureTurns[12].trace_id : genTraceId()
    setTraceId(nextTrace); onTrace?.(nextTrace, command)
    if (isFixture()) {
      setState('running'); timer.current = setTimeout(() => { setState('done'); setReply('离线示例：未发送到网关。下方显示样例链路。') }, 300)
      return
    }
    let settled = false
    let sent = false
    let activeSocket: WebSocket | null = null
    const fail = (message: string, failedState: CommandState, failureReason: CommandReason) => {
      if (settled || (activeSocket && socket.current !== activeSocket)) return
      settled = true; setReply(message); setState(failedState); setReason(failureReason); close()
    }
    const lost = (failureReason: 'connection-lost' | 'transport-error') => sent
      ? fail('请求已发送，但结果未取得，是否执行未知。请在轮次页核对观测记录。', 'failed', failureReason)
      : fail('无法建立 Edge Gateway 连接，请求尚未发送。', 'unreachable', 'connect-error')
    try {
      const ws = new WebSocket(EDGE_WS_URL)
      activeSocket = ws; socket.current = ws
      timer.current = setTimeout(() => sent
        ? fail('请求超时：结果未取得，是否执行未知。请在轮次页核对观测记录。', 'failed', 'result-timeout')
        : fail('连接超时，请求尚未发送。', 'unreachable', 'connect-timeout'), 35000)
      ws.onopen = () => {
        if (socket.current !== ws) return
        try {
          ws.send(JSON.stringify({ text: command, session_id: 'dashboard-' + new Date().toISOString().slice(0, 10), is_confirmation: false, meta: { trace_id: nextTrace } }))
          sent = true; setState('running')
        } catch { fail('请求未成功发送，网关连接已中断。', 'unreachable', 'send-error') }
      }
      ws.onmessage = event => {
        if (socket.current !== ws || settled) return
        try {
          const message = JSON.parse(String(event.data))
          if (message.type === 'speech_delta' && typeof message.delta === 'string') setReply(previous => previous + message.delta)
          else if (message.type === 'final') { settled = true; setReply(typeof message.speech === 'string' && message.speech ? message.speech : '请求已结束，未返回话术。'); setState('done'); setReason(''); close() }
          else if (message.type === 'error') fail(typeof message.message === 'string' && message.message ? message.message : '网关返回了错误。', 'failed', 'gateway-error')
        } catch { fail('响应格式错误：结果未取得，是否执行未知。', 'failed', 'invalid-response') }
      }
      ws.onerror = () => { if (socket.current === ws) lost('transport-error') }
      ws.onclose = () => { if (socket.current === ws && !settled) lost('connection-lost') }
    } catch { fail('无法建立 Edge Gateway 连接，请求尚未发送。', 'unreachable', 'connect-error') }
  }
  const busy = state === 'connecting' || state === 'running'
  return <section className="panel command-console" data-command-state={state} data-command-reason={reason || undefined}>
    <div className="row wrap"><h2>指令台</h2><p className="caption">发送 Edge Gateway，和 HMI 走同一条路；车控仍经 VAL 与确认</p></div>
    <form className="row" onSubmit={e => { e.preventDefault(); send() }}><Input className="grow" aria-label="指令" placeholder="说一句，比如「空调调到 26 度」" value={text} disabled={busy} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && e.nativeEvent.isComposing) e.preventDefault() }} /><Button kind="primary" type="submit" disabled={busy || !text.trim()}>发送</Button></form>
    <div className="row wrap" role="group" aria-label="快捷指令">{QUICK.map(command => <FilterChip key={command} disabled={busy} onClick={() => send(command)}>{command}</FilterChip>)}</div>
    <div className="command-output" aria-live="polite"><div className="row wrap"><Tag tone={state === 'failed' || state === 'unreachable' ? 'critical' : state === 'running' || state === 'connecting' ? 'pending' : 'neutral'}>{UNKNOWN_RESULT.has(reason) ? '结果未取得' : reason === 'send-error' ? '请求未发出' : STATES[state]}</Tag>
      {traceId && <Button size="sm" kind="ghost" onClick={() => navigate({ view: 'turns', trace: traceId, query: '' })}>#{traceId.slice(0, 12)}</Button>}
      {hasEvidence && <Tag tone="accent">{changes?.length ? `车身变化 ${new Set(changes.map(change => change.key)).size} 项` : '无变化'}</Tag>}
      {state === 'done' && !hasEvidence && <span className="caption">未采到车身变化证据</span>}
    </div><p>{reply || '说一句，观察本句链路与车辆状态。'}</p></div>
  </section>
}
