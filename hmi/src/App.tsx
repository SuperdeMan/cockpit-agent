// 座舱 HMI 外壳：WebSocket 连接（带重连）+ 视图路由（对话/设置）+ 消息状态机。
// 消息流：用户发送 → 立刻插入助手"思考中"占位 → final 替换 / speech_delta 流式填充。
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSettings, buildMeta } from './settings'
import {
  buildRequestLocationMeta,
  requestCurrentLocation,
  shouldRequestLocationConsent,
} from './location.mjs'
import { StatusBar } from './components/StatusBar'
import { ChatView } from './components/ChatView'
import { Composer } from './components/Composer'
import { SettingsPanel } from './components/SettingsPanel'
import {
  appendTTSDelta,
  finishTTSReply,
  startTTSReply,
  stopTTS,
} from './audio'
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
  const { settings, update } = useSettings()
  const [messages, setMessages] = useState<Msg[]>([])
  const [connected, setConnected] = useState(false)
  const [awaitConfirm, setAwaitConfirm] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [currentLocation, setCurrentLocation] = useState<any>(null)
  const [locationStatus, setLocationStatus] = useState('未使用当前位置')
  const [pendingLocationText, setPendingLocationText] = useState<string | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const locationRefreshRequestedRef = useRef(false)
  const pendingIdRef = useRef<string | null>(null)
  const settingsRef = useRef<Settings>(settings)
  settingsRef.current = settings // 始终保留最新设置，避免 ws 回调读到陈旧闭包

  useEffect(() => {
    if (!settings.ttsEnabled || !settings.autoplay) stopTTS()
  }, [settings.ttsEnabled, settings.autoplay])

  const refreshCurrentLocation = useCallback(async () => {
    setLocationStatus('正在获取当前位置…')
    try {
      const position = await requestCurrentLocation()
      setCurrentLocation(position)
      setLocationStatus(`定位已启用，当前精度约 ${Math.round(position.accuracyM)} 米`)
      return position
    } catch (error: any) {
      setCurrentLocation(null)
      setLocationStatus(error?.code === 1 ? '浏览器定位授权被拒绝，请在浏览器站点权限中允许后重试' : '暂时无法获取当前位置，请稍后重试')
      return null
    }
  }, [])

  useEffect(() => {
    if (settings.locationEnabled) {
      if (locationRefreshRequestedRef.current) {
        locationRefreshRequestedRef.current = false
        return
      }
      void refreshCurrentLocation()
    } else {
      setCurrentLocation(null)
      setLocationStatus('定位权限未启用')
    }
  }, [settings.locationEnabled, refreshCurrentLocation])

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
      stopTTS()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleEvent = useCallback((data: any) => {
    const s = settingsRef.current
    if (data.type === 'speech_delta') {
      // 流式逐字：把 pending 占位转为 streaming，并追加 delta。
      // 若当前没有活跃占位（如混合意图里本地已 final、云端流式刚开始），
      // 新开一个助手气泡——否则这段 delta 会无处归属被丢弃。
      const delta = data.delta || ''
      if (s.ttsEnabled && s.autoplay && delta) {
        appendTTSDelta(delta).catch(() => {/* 播放失败静默 */})
      }
      let id = pendingIdRef.current
      if (!id) {
        id = uid()
        pendingIdRef.current = id
      }
      const targetId = id
      setMessages((m) =>
        m.some((x) => x.id === targetId)
          ? m.map((msg) =>
              msg.id === targetId
                ? { ...msg, pending: false, streaming: true, text: msg.text + delta }
                : msg,
            )
          : [...m, { id: targetId, role: 'assistant', text: delta, streaming: true } as Msg],
      )
      return
    }
    if (data.type === 'action') {
      // 流式期间单独下发的动作卡（如 T2 循环中间步骤）：附到当前气泡；
      // 没有活跃气泡则新开一个，避免动作被静默丢弃。
      const action = data.action
      let id = pendingIdRef.current
      if (!id) {
        id = uid()
        pendingIdRef.current = id
      }
      const targetId = id
      setMessages((m) =>
        m.some((x) => x.id === targetId)
          ? m.map((msg) =>
              msg.id === targetId
                ? { ...msg, pending: false, actions: [...(msg.actions || []), action] }
                : msg,
            )
          : [...m, { id: targetId, role: 'assistant', text: '', streaming: true, actions: [action] } as Msg],
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
        uiCard: data.ui_card,
      }
      setMessages((m) =>
        id && m.some((x) => x.id === id)
          ? m.map((msg) => (msg.id === id ? { ...msg, ...final } : msg))
          : [...m, { id: uid(), role: 'assistant', ...final } as Msg],
      )
      setAwaitConfirm(!!data.need_confirm)
      if (s.ttsEnabled && s.autoplay && data.speech) {
        finishTTSReply(data.speech).catch(() => {/* 播放失败静默 */})
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

  const dispatch = (text: string, isConfirmation: boolean, locationOverride?: any) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const s = settingsRef.current
    if (s.ttsEnabled && s.autoplay) startTTSReply(AUDIO_API, s.voiceId)
    else stopTTS()
    ws.send(
      JSON.stringify({
        text,
        session_id: SESSION,
        is_confirmation: isConfirmation,
        meta: {
          ...buildMeta(s),
          ...buildRequestLocationMeta(
            locationOverride !== undefined || s.locationEnabled,
            locationOverride !== undefined ? locationOverride : currentLocation,
          ),
        },
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
    if (shouldRequestLocationConsent(text, settingsRef.current.locationEnabled)) {
      setPendingLocationText(text)
      setMessages((m) => [...m, {
        id: uid(),
        role: 'assistant',
        text: '这个请求需要使用当前位置，以便提供准确结果。是否允许座舱助手获取当前位置？您也可以拒绝后直接告诉我城市或地点。',
        needConfirm: true,
      } as Msg])
      setAwaitConfirm(true)
      return
    }
    dispatch(text, false)
  }

  const confirm = (reply: '确认' | '取消') => {
    setMessages((m) => [...m, { id: uid(), role: 'user', text: reply }])
    setAwaitConfirm(false)
    if (pendingLocationText) {
      const text = pendingLocationText
      setPendingLocationText(null)
      if (reply === '确认') {
        void enableLocation().then((position) => {
          if (position) dispatch(text, false, position)
          else setMessages((m) => [...m, {
            id: uid(), role: 'assistant',
            text: '没有获取到当前位置。您可以在设置中重试授权，或直接告诉我城市或地点。', error: true,
          }])
        })
      } else {
        setMessages((m) => [...m, {
          id: uid(), role: 'assistant',
          text: '好的，请直接告诉我城市、出发地或附近地标，我会按您提供的位置继续处理。',
        }])
      }
      return
    }
    dispatch(reply, true)
  }

  const enableLocation = async () => {
    update({ locationEnabled: true })
    // 保持在用户点击的调用栈中，确保首次浏览器授权能正常弹出。
    locationRefreshRequestedRef.current = true
    const position = await refreshCurrentLocation()
    if (!position) update({ locationEnabled: false })
    return position
  }

  const setLocationEnabled = (enabled: boolean) => {
    if (enabled) {
      // 保持在用户点击的调用栈中，确保首次浏览器授权能正常弹出。
      void enableLocation()
    } else {
      update({ locationEnabled: false })
      setCurrentLocation(null)
      setLocationStatus('已关闭定位使用并清除本地坐标')
    }
  }

  const requestLocation = () => setLocationEnabled(true)

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
        <SettingsPanel
          audioApi={AUDIO_API}
          sessionId={SESSION}
          location={currentLocation}
          locationEnabled={settings.locationEnabled}
          locationStatus={locationStatus}
          onRequestLocation={requestLocation}
          onLocationEnabledChange={setLocationEnabled}
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  )
}
