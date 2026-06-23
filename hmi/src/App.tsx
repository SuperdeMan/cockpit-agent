// 座舱 HMI 外壳：WebSocket 连接（带重连）+ 视图路由（对话/设置）+ 消息状态机。
// 消息流：用户发送 → 立刻插入助手"思考中"占位 → final 替换 / speech_delta 流式填充。
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSettings, buildMeta } from './settings'
import {
  buildRequestLocationMeta,
  requestCurrentLocation,
  shouldRequestLocationConsent,
  isLocationDependent,
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
import { poiSelectionIndex, isRefreshRequest } from './nav.mjs'

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
  // 上一条 poi_list 的候选名（供「第一个/第二个」语音选择就近导航；见 resolvePoiSelection）
  const lastPoiNamesRef = useRef<string[] | null>(null)
  // 充电目的地候选（dest_choice）名：「第N个」回填目的地槽位续接规划，而非发起导航
  const lastDestChoiceRef = useRef<string[] | null>(null)
  // 顺路停靠候选（waypoint_choice）：「第N个」派发「导航去{目的地}途经{名称}」→ 落途经点
  const lastWaypointChoiceRef = useRef<{ destination: string; names: string[] } | null>(null)
  // 就近类目候选（plain poi_list）上下文：供「换一批/换一个」翻页取下一批不同结果。
  // 只存类目关键词（如"粤菜馆"），换一批时重发干净的「导航去附近的{关键词}」——
  // 复杂指令下不会把原句里的车控（空调/座椅/氛围灯）又执行一遍。
  const categoryRef = useRef<{ keyword: string; page: number } | null>(null)
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
      // 记录候选名供下一轮「第N个」选择：充电目的地候选(dest_choice)→回填目的地槽位；
      // 普通导航 poi_list→就近导航（见 send）
      {
        const c: any = data.ui_card
        const names = c?.type === 'poi_list'
          ? (c.items || []).map((it: any) => it.name).filter(Boolean) : null
        lastDestChoiceRef.current = null
        lastWaypointChoiceRef.current = null
        lastPoiNamesRef.current = null
        if (c?.type === 'poi_list' && c.purpose === 'dest_choice') {
          lastDestChoiceRef.current = names
        } else if (c?.type === 'poi_list' && c.purpose === 'waypoint_choice') {
          lastWaypointChoiceRef.current = { destination: c.destination || '', names: names || [] }
        } else if (c?.type === 'poi_list') {
          lastPoiNamesRef.current = names
          // 就近类目候选：记关键词供「换一批」翻页。同一关键词的翻页保留页码，换类目则从第 1 页起。
          const kw = c.keyword || ''
          categoryRef.current = kw
            ? (categoryRef.current?.keyword === kw ? categoryRef.current : { keyword: kw, page: 1 })
            : null
        }
      }
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

  const dispatch = (text: string, isConfirmation: boolean, locationOverride?: any,
                    metaExtra?: Record<string, string>) => {
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
          ...(metaExtra || {}),
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
    // 「换一批/换一个」：对上一条就近类目候选翻页，重发干净的「导航去附近的{关键词}」+ 下一页
    // （只重搜 POI，不会把复杂原句里的车控空调/座椅/氛围灯又执行一遍），并带最新定位。
    if (isRefreshRequest(text) && categoryRef.current) {
      const page = categoryRef.current.page + 1
      categoryRef.current = { ...categoryRef.current, page }
      const kw = categoryRef.current.keyword
      void refreshCurrentLocation().then((position) =>
        dispatch(`导航去附近的${kw}`, false, position, { poi_page: String(page) }))
      return
    }
    // 顺路停靠途经点候选「第N个」：派发「导航去{目的地}途经{名称}」→ navigate.waypoints
    const wp = lastWaypointChoiceRef.current
    if (wp && wp.names.length && wp.destination) {
      const idx = poiSelectionIndex(text)
      if (idx >= 0 && idx < wp.names.length) {
        lastWaypointChoiceRef.current = null
        dispatch(`导航去${wp.destination}途经${wp.names[idx]}`, false)
        return
      }
    }
    // 充电目的地候选「第N个」：派发候选名本身 → 编排器回填目的地槽位续接规划（不改写为导航）
    const choices = lastDestChoiceRef.current
    if (choices && choices.length) {
      const idx = poiSelectionIndex(text)
      if (idx >= 0 && idx < choices.length) {
        lastDestChoiceRef.current = null
        dispatch(choices[idx], false)
        return
      }
    }
    // 「第一个/第二个」：对照上一条 poi_list 候选 → 改写为「导航去{名称}」，修「第一个→处理失败」
    const names = lastPoiNamesRef.current
    if (names && names.length) {
      const idx = poiSelectionIndex(text)
      if (idx >= 0 && idx < names.length) {
        lastPoiNamesRef.current = null
        dispatch(`导航去${names[idx]}`, false)
        return
      }
    }
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
    // 定位已开启 + 位置相关查询（导航/就近/我在哪/天气）：先实时刷新一次定位再发，
    // 用最新坐标而非可能为空/陈旧的缓存——否则"导航去最近的粤菜馆"会误报"先开定位"。
    if (settingsRef.current.locationEnabled && isLocationDependent(text)) {
      void refreshCurrentLocation().then((position) => dispatch(text, false, position))
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
