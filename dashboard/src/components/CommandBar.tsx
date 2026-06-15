import { useEffect, useRef, useState } from 'react'

const EDGE =
  (import.meta.env.VITE_EDGE_GATEWAY_URL as string | undefined) ||
  'http://localhost:8090'
const WS_URL = EDGE.replace(/^http/, 'ws') + '/ws'

type CommandState = 'idle' | 'connecting' | 'running' | 'done' | 'error'

const QUICK = [
  '空调调到26度',
  '打开主驾座椅加热',
  '氛围灯调成绿色',
  '导航去机场顺便订今晚的餐',
  '打开后备箱',
]

export function genTraceId(): string {
  const bytes = new Uint8Array(8)
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256)
    }
  }
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join(
    '',
  )
}

export function CommandBar({ onTrace }: { onTrace?: (traceId: string) => void }) {
  const [text, setText] = useState('空调调到26度')
  const [state, setState] = useState<CommandState>('idle')
  const [traceId, setTraceId] = useState('')
  const [reply, setReply] = useState('')
  const websocketRef = useRef<WebSocket | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>()

  const close = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    websocketRef.current?.close()
    websocketRef.current = null
  }

  useEffect(() => close, [])

  const send = (override?: string) => {
    const command = (override ?? text).trim()
    if (!command || state === 'connecting' || state === 'running') return
    if (override !== undefined) setText(override)

    close()
    const nextTraceId = genTraceId()
    setTraceId(nextTraceId)
    setReply('')
    setState('connecting')
    onTrace?.(nextTraceId)

    const websocket = new WebSocket(WS_URL)
    websocketRef.current = websocket
    timeoutRef.current = setTimeout(() => {
      setState('error')
      setReply('请求超时')
      close()
    }, 35_000)

    websocket.onopen = () => {
      setState('running')
      websocket.send(
        JSON.stringify({
          text: command,
          session_id: 'dashboard-observability',
          is_confirmation: false,
          meta: { trace_id: nextTraceId },
        }),
      )
    }
    websocket.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data))
        if (message.type === 'speech_delta' && message.delta) {
          setReply((previous) => previous + message.delta)
        } else if (message.type === 'final') {
          setReply(message.speech || '执行完成')
          setState('done')
          close()
        } else if (message.type === 'error') {
          setReply(message.message || '执行失败')
          setState('error')
          close()
        }
      } catch {
        setReply('响应格式错误')
        setState('error')
        close()
      }
    }
    websocket.onerror = () => {
      setReply('Edge Gateway 连接失败')
      setState('error')
      close()
    }
  }

  const busy = state === 'connecting' || state === 'running'

  return (
    <section className="panel">
      <div className="panel__head">
        <div className="panel__title">
          <h2>对照实验</h2>
          <span className="en">Command</span>
        </div>
        <span className="panel__tag">发指令 → 看链路</span>
      </div>

      <div className="cmd">
        <div className="cmd__row">
          <input
            className="cmd__input"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') send()
            }}
            placeholder="发一条指令，观察链路与状态变化"
          />
          <button
            className="cmd__send"
            type="button"
            onClick={() => send()}
            disabled={!text.trim() || busy}
          >
            发射
          </button>
        </div>

        <div className="cmd__chips">
          {QUICK.map((item) => (
            <span key={item} className="chip" onClick={() => send(item)}>
              {item}
            </span>
          ))}
        </div>

        <div className="cmd__out">
          <div className="cmd__trace">
            TRACE {traceId ? `#${traceId.slice(0, 12)}` : '--'}
            <span className={`cmd__state cmd__state--${state}`}>
              {state.toUpperCase()}
            </span>
          </div>
          <div className="cmd__reply">{reply || '等待指令…'}</div>
        </div>
      </div>
    </section>
  )
}
