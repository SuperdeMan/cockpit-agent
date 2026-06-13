import { useEffect, useRef, useState, useCallback } from 'react'

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
const AUDIO_API = (import.meta.env.VITE_AUDIO_API_URL as string) || 'http://localhost:50059'
const SESSION = 'demo-' + Math.random().toString(36).slice(2, 8)

const QUICK = ['打开空调26度', '关闭空调', '播放音乐', '附近的充电站', '讲个笑话', '导航去首都机场']

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [connected, setConnected] = useState(false)
  const [awaitConfirm, setAwaitConfirm] = useState(false)
  const [recording, setRecording] = useState(false)
  const [audioEnabled, setAudioEnabled] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  useEffect(() => {
    const ws = new WebSocket(WS_URL)
    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data)
      if (data.type === 'final') {
        const msg: Msg = {
          role: 'assistant', text: data.speech || '', actions: data.actions,
          needConfirm: !!data.need_confirm, followUp: data.follow_up,
        }
        setMessages((m) => [...m, msg])
        setAwaitConfirm(!!data.need_confirm)
        // TTS 播放
        if (audioEnabled && data.speech) {
          playTTS(data.speech)
        }
      } else if (data.type === 'error') {
        setMessages((m) => [...m, { role: 'assistant', text: '出错了：' + data.message }])
        setAwaitConfirm(false)
      }
    }
    wsRef.current = ws
    return () => ws.close()
  }, [audioEnabled])

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

  const replyConfirm = (text: '确认' | '取消') => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    setMessages((m) => [...m, { role: 'user', text }])
    wsRef.current.send(JSON.stringify({ text, session_id: SESSION, is_confirmation: true }))
    setAwaitConfirm(false)
  }

  // ─── ASR：录音 → 识别 ───

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
      chunksRef.current = []
      recorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      recorder.onstop = async () => {
        stream.getTracks().forEach(t => t.stop())
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        await recognizeAudio(blob)
      }
      recorder.start()
      mediaRecorderRef.current = recorder
      setRecording(true)
    } catch (e) {
      console.error('录音失败:', e)
      alert('无法访问麦克风，请检查浏览器权限。')
    }
  }

  const stopRecording = () => {
    mediaRecorderRef.current?.stop()
    setRecording(false)
  }

  const recognizeAudio = async (blob: Blob) => {
    const reader = new FileReader()
    reader.onloadend = async () => {
      const base64 = (reader.result as string).split(',')[1]
      try {
        const resp = await fetch(`${AUDIO_API}/api/asr`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ audio: base64, format: 'webm', language: 'zh' }),
        })
        const data = await resp.json()
        if (data.text) {
          send(data.text)
        } else {
          console.error('ASR 无结果:', data)
        }
      } catch (e) {
        console.error('ASR 请求失败:', e)
      }
    }
    reader.readAsDataURL(blob)
  }

  // ─── TTS：文本 → 播放 ───

  const playTTS = async (text: string) => {
    try {
      const resp = await fetch(`${AUDIO_API}/api/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voice_id: '冰糖', format: 'wav' }),
      })
      const data = await resp.json()
      if (data.audio) {
        const audioBytes = Uint8Array.from(atob(data.audio), c => c.charCodeAt(0))
        const audioBlob = new Blob([audioBytes], { type: `audio/${data.format || 'wav'}` })
        const url = URL.createObjectURL(audioBlob)
        const audio = new Audio(url)
        audio.onended = () => URL.revokeObjectURL(url)
        audio.play()
      }
    } catch (e) {
      console.error('TTS 请求失败:', e)
    }
  }

  return (
    <div className="app">
      <header className="bar">
        <div className="logo">🚗 座舱助手 · 小舟</div>
        <div className="controls">
          <button
            className={'audio-toggle ' + (audioEnabled ? 'on' : 'off')}
            onClick={() => setAudioEnabled(!audioEnabled)}
            title={audioEnabled ? '关闭语音播报' : '开启语音播报'}
          >
            {audioEnabled ? '🔊' : '🔇'}
          </button>
          <div className={'status ' + (connected ? 'on' : 'off')}>
            <span className="dot" /> {connected ? '已连接' : '连接中…'}
          </div>
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
        <button
          className={'mic ' + (recording ? 'recording' : '')}
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          title="按住说话"
        >
          {recording ? '🔴' : '🎤'}
        </button>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(input)}
          placeholder="输入指令或按住麦克风说话…"
        />
        <button className="send" onClick={() => send(input)}>发送</button>
      </div>
    </div>
  )
}
