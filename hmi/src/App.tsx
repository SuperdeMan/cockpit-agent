// 座舱 HMI 外壳：WebSocket 连接（带重连）+ 视图路由（对话/设置）+ 消息状态机。
// 消息流：用户发送 → 立刻插入助手"思考中"占位 → final 替换 / speech_delta 流式填充。
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSettings, buildMeta } from './settings'
import { StatusBar } from './components/StatusBar'
import { ChatView } from './components/ChatView'
import { Composer } from './components/Composer'
import { SettingsPanel } from './components/SettingsPanel'
import { playTTS } from './audio'
import type { Msg, Settings } from './types'

const GATEWAY = (import.meta.env.VITE_EDGE_GATEWAY_URL as string) || 'http://localhost:8090'
const WS_URL = GATEWAY.replace(/^http/, 'ws') + '/ws'
const AUDIO_API = (import.meta.env.VITE_AUDIO_API_URL as string) || 'http://localhost:50059'
const SESSION = 'demo-' + Math.random().toString(36).slice(2, 8)

const uid = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2)

export default function App() {
  const { settings } = useSettings()
  const [messages, setMessages] = useState<Msg[]>([])
  const [connected, setConnected] = useState(false)
  const [awaitConfirm, setAwaitConfirm] = useState(false)
  const [showSettings, setShowSettings] = useState(false)

  const wsRef = useRef<WebSocket | null>(null)
  const pendingIdRef = useRef<string | null>(null)
  const settingsRef = useRef<Settings>(settings)
  settingsRef.current = settings // 始终保留最新设置，避免 ws 回调读到陈旧闭包

  // ─── WebSocket 连接 + 自动重连 ───
  useEffect(() => {
    let closed = false
    let retry: number | undefined

    const connect = () => {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = window.setTimeout(connect, 1500)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data))
    }
    connect()

    return () => {
      closed = true
      if (retry) clearTimeout(retry)
      wsRef.current?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleEvent = useCallback((data: any) => {
    const s = settingsRef.current
    if (data.type === 'speech_delta') {
      // 流式逐字：把 pending 占位转为 streaming，并追加 delta
      const id = pendingIdRef.current
      setMessages((m) =>
        m.map((msg) =>
          msg.id === id
            ? { ...msg, pending: false, streaming: true, text: msg.text + (data.delta || '') }
            : msg,
        ),
      )
      return
    }
    if (data.type === 'final') {
      const id = pendingIdRef.current
      pendingIdRef.current = null
      const final: Partial<Msg> = {
        pending: false,
        streaming: false,
        text: data.speech || '',
        actions: data.actions,
        needConfirm: !!data.need_confirm,
        followUp: data.follow_up,
      }
      setMessages((m) =>
        id && m.some((x) => x.id === id)
          ? m.map((msg) => (msg.id === id ? { ...msg, ...final } : msg))
          : [...m, { id: uid(), role: 'assistant', ...final } as Msg],
      )
      setAwaitConfirm(!!data.need_confirm)
      if (s.ttsEnabled && s.autoplay && data.speech) {
        playTTS(AUDIO_API, data.speech, s.voiceId).catch(() => {/* 播放失败静默 */})
      }
      return
    }
    if (data.type === 'error') {
      pendingIdRef.current = null
      setMessages((m) => [
        ...m.filter((x) => !x.pending),
        { id: uid(), role: 'assistant', text: '出错了：' + data.message, error: true },
      ])
      setAwaitConfirm(false)
    }
  }, [])

  const dispatch = (text: string, isConfirmation: boolean) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const s = settingsRef.current
    ws.send(
      JSON.stringify({
        text,
        session_id: SESSION,
        is_confirmation: isConfirmation,
        meta: buildMeta(s), // 会话级偏好透传（后端忽略未知字段，向前兼容）
      }),
    )
    // 立刻插入"思考中"占位 —— 开放域慢响应也有即时反馈
    const pendingId = uid()
    pendingIdRef.current = pendingId
    setMessages((m) => [...m, { id: pendingId, role: 'assistant', text: '', pending: true }])
  }

  const send = (text: string) => {
    setMessages((m) => [...m, { id: uid(), role: 'user', text }])
    setAwaitConfirm(false)
    dispatch(text, false)
  }

  const confirm = (reply: '确认' | '取消') => {
    setMessages((m) => [...m, { id: uid(), role: 'user', text: reply }])
    setAwaitConfirm(false)
    dispatch(reply, true)
  }

  return (
    <div className="app">
      <div className="aurora" aria-hidden>
        <span className="a1" />
        <span className="a2" />
        <span className="grid-lines" />
      </div>

      <StatusBar connected={connected} onOpenSettings={() => setShowSettings(true)} />
      <ChatView messages={messages} awaitConfirm={awaitConfirm} onConfirm={confirm} onQuick={send} />
      <Composer audioApi={AUDIO_API} onSend={send} hint={connected ? undefined : '正在连接座舱服务…'} />

      {showSettings && (
        <SettingsPanel audioApi={AUDIO_API} sessionId={SESSION} onClose={() => setShowSettings(false)} />
      )}
    </div>
  )
}
