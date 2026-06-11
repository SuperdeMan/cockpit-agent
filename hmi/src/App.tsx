import { useEffect, useRef, useState } from 'react'

type Action = { type: string; payload?: Record<string, unknown>; require_confirm?: boolean }
type Msg = {
  role: 'user' | 'assistant'
  text: string
  actions?: Action[]
  needConfirm?: boolean
  followUp?: string
}

const GATEWAY = (import.meta.env.VITE_EDGE_GATEWAY_URL as string) || 'http://localhost:8090'
const WS_URL = GATEWAY.replace(/^http/, 'ws') + '/ws'
const SESSION = 'demo-' + Math.random().toString(36).slice(2, 8)

const QUICK = ['打开空调26度', '关闭空调', '播放音乐', '附近的充电站', '讲个笑话', '导航去首都机场']

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [connected, setConnected] = useState(false)
  const [awaitConfirm, setAwaitConfirm] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const ws = new WebSocket(WS_URL)
    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data)
      if (data.type === 'final') {
        setMessages((m) => [...m, {
          role: 'assistant', text: data.speech || '', actions: data.actions,
          needConfirm: !!data.need_confirm, followUp: data.follow_up,
        }])
        setAwaitConfirm(!!data.need_confirm)
      } else if (data.type === 'error') {
        setMessages((m) => [...m, { role: 'assistant', text: '出错了：' + data.message }])
        setAwaitConfirm(false)
      }
    }
    wsRef.current = ws
    return () => ws.close()
  }, [])

  useEffect(() => {
    listRef.current?.scrollTo(0, listRef.current.scrollHeight)
  }, [messages])

  const send = (text: string) => {
    const t = text.trim()
    if (!t || wsRef.current?.readyState !== WebSocket.OPEN) return
    setMessages((m) => [...m, { role: 'user', text: t }])
    wsRef.current.send(JSON.stringify({ text: t, session_id: SESSION }))
    setAwaitConfirm(false)
    setInput('')
  }

  // 回应待确认任务：带 is_confirmation 标记，云端编排器据此续接挂起计划
  const replyConfirm = (text: '确认' | '取消') => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    setMessages((m) => [...m, { role: 'user', text }])
    wsRef.current.send(JSON.stringify({ text, session_id: SESSION, is_confirmation: true }))
    setAwaitConfirm(false)
  }

  return (
    <div className="app">
      <header className="bar">
        <div className="logo">🚗 座舱助手 · 小舟</div>
        <div className={'status ' + (connected ? 'on' : 'off')}>
          <span className="dot" /> {connected ? '已连接' : '连接中…'}
        </div>
      </header>

      <div className="chat" ref={listRef}>
        {messages.length === 0 && (
          <div className="hint">说点什么，或点下方快捷指令试试 👇</div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'row ' + m.role}>
            <div className={'bubble ' + m.role}>
              <div className="text">{m.text}</div>
              {m.actions?.map((a, j) => (
                <div key={j} className="action">
                  <span className="tag">{a.type}</span>
                  <span>{(a.payload?.command as string) ?? JSON.stringify(a.payload)}</span>
                  {a.require_confirm && <span className="confirm">需确认</span>}
                </div>
              ))}
              {m.followUp && <div className="followup">{m.followUp}</div>}
              {m.needConfirm && awaitConfirm && i === messages.length - 1 && (
                <div className="confirm-bar">
                  <button className="yes" onClick={() => replyConfirm('确认')}>确认</button>
                  <button className="no" onClick={() => replyConfirm('取消')}>取消</button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="quick">
        {QUICK.map((q) => (
          <button key={q} onClick={() => send(q)}>{q}</button>
        ))}
      </div>

      <div className="composer">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(input)}
          placeholder="输入指令（模拟语音）…"
        />
        <button className="send" onClick={() => send(input)}>发送</button>
      </div>
    </div>
  )
}
