import { useEffect, useRef, useState } from 'react'

const EDGE =
  (import.meta.env.VITE_EDGE_GATEWAY_URL as string | undefined) ||
  'http://localhost:8090'
const WS_URL = EDGE.replace(/^http/, 'ws') + '/ws'

type CommandState = 'idle' | 'connecting' | 'running' | 'done' | 'error'

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

export function CommandBar({
  onTrace,
}: {
  onTrace?: (traceId: string) => void
}) {
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

  const send = () => {
    const command = text.trim()
    if (!command || state === 'connecting' || state === 'running') return

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

  return (
    <section className="panel command-panel" aria-labelledby="command-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CONTROLLED EXPERIMENT</p>
          <h2 id="command-title">对照实验命令</h2>
        </div>
        <span className={`command-state command-state--${state}`}>
          {state.toUpperCase()}
        </span>
      </div>
      <div className="command-form">
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') send()
          }}
          placeholder="发一条指令观察链路与状态"
        />
        <button
          type="button"
          onClick={send}
          disabled={!text.trim() || state === 'connecting' || state === 'running'}
        >
          发射
        </button>
      </div>
      <div className="command-output">
        <span>TRACE {traceId ? `#${traceId}` : '--'}</span>
        <p>{reply || '等待指令'}</p>
      </div>
    </section>
  )
}
