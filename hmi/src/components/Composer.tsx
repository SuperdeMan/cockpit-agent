// 底部输入区（Aurora Glass）：快捷指令轨 + 小舟光球 + 麦克风 + 文本输入 + 发送。
// 语音输入两条路：①流式实时上屏（StreamingRecognizer→WS，partial 写进输入框）；
// ②批处理（MicController→recognize，录完再出）。流式失败本会话无感回退批处理。
import { useEffect, useRef, useState } from 'react'
import { useSettings } from '../settings'
import {
  MicController, micSupported, secureContextOk, recognize, stopTTS,
  StreamingRecognizer, streamingAsrSupported, asrStreamUrl, type RecordResult,
} from '../audio'
import { AuroraOrb } from './aurora'

type MicState = 'idle' | 'recording' | 'transcribing'

export function Composer({
  audioApi,
  onSend,
  hint,
}: {
  audioApi: string
  onSend: (text: string) => void
  hint?: string
}) {
  const { settings } = useSettings()
  const [input, setInput] = useState('')
  const [mic, setMic] = useState<MicState>('idle')
  const [notice, setNotice] = useState<string>('')
  const ctrlRef = useRef<MicController | null>(null)
  if (!ctrlRef.current) ctrlRef.current = new MicController()
  const streamRef = useRef<StreamingRecognizer | null>(null)
  if (!streamRef.current) streamRef.current = new StreamingRecognizer()
  // 流式模式：能力支持 + 设置非 off；一旦流式失败则本会话回退批处理
  const streamModeRef = useRef(streamingAsrSupported() && settings.asrProvider !== 'off')
  useEffect(() => {
    streamModeRef.current = streamingAsrSupported() && settings.asrProvider !== 'off'
  }, [settings.asrProvider])

  const supported = micSupported() && secureContextOk()

  useEffect(() => {
    if (!micSupported()) setNotice('当前浏览器不支持录音')
    else if (!secureContextOk()) setNotice('麦克风需在 localhost 或 HTTPS 下使用')
  }, [])

  const send = (text: string) => {
    const t = text.trim()
    if (!t) return
    onSend(t)
    setInput('')
  }

  const onResult = async (r: RecordResult) => {
    if (!r) {
      setMic('idle')
      return
    }
    setMic('transcribing')
    try {
      const text = await recognize(audioApi, r.blob, r.format, settings.asrLanguage)
      if (text) send(text)
      else setNotice('没听清，请再说一次')
    } catch (e) {
      setNotice('识别失败：' + (e instanceof Error ? e.message : '请重试'))
    } finally {
      setMic('idle')
    }
  }

  // 流式：partial 实时写进输入框，final 自动发送；出错回退批处理
  const beginStream = async () => {
    setMic('recording')
    const model = settings.asrProvider === 'dashscope' ? settings.asrModel : ''
    await streamRef.current!.start(asrStreamUrl(audioApi), {
      language: settings.asrLanguage,
      provider: settings.asrProvider,
      model,
      onPartial: (t) => setInput(t),
      onFinal: (t) => {
        setMic('idle')
        if (t.trim()) send(t)
        else setInput('')
      },
      onError: (msg) => {
        streamModeRef.current = false // 本会话回退批处理
        setMic('idle')
        setInput('')
        setNotice('实时识别暂不可用，已切换经典模式：' + msg)
      },
    })
  }

  const beginRecord = async () => {
    if (!supported || mic !== 'idle') return
    stopTTS()
    setNotice('')
    try {
      if (streamModeRef.current) {
        await beginStream()
      } else {
        setMic('recording')
        await ctrlRef.current!.start(settings.listenSeconds * 1000, onResult)
      }
    } catch {
      setMic('idle')
      setNotice('无法访问麦克风，请检查权限')
    }
  }

  const endRecord = () => {
    if (mic !== 'recording') return
    if (streamModeRef.current) {
      streamRef.current!.stop()
      setMic('transcribing') // 等定稿（光球 thinking）
    } else {
      ctrlRef.current!.stop()
    }
  }

  // 按住说话：press/release；点按切换：click 切换
  const holdHandlers =
    settings.micMode === 'hold'
      ? {
          onMouseDown: beginRecord,
          onMouseUp: endRecord,
          onMouseLeave: endRecord,
          onTouchStart: (e: React.TouchEvent) => { e.preventDefault(); beginRecord() },
          onTouchEnd: (e: React.TouchEvent) => { e.preventDefault(); endRecord() },
        }
      : {
          onClick: () => (mic === 'recording' ? endRecord() : beginRecord()),
        }

  // 语音按钮即小舟光球：录音→speaking（波纹）、识别中→thinking（律动）、空闲→idle（呼吸）
  const orbState = mic === 'recording' ? 'speaking' : mic === 'transcribing' ? 'thinking' : 'idle'

  return (
    <div className="au-composer">
      <div className="au-quick-rail">
        {settings.quickCommands.map((q) => (
          <button key={q} className="au-quick-chip" onClick={() => send(q)}>
            {q}
          </button>
        ))}
      </div>

      {(notice || hint) && <div className="au-composer-notice">{notice || hint}</div>}

      <div className="au-composer-bar">
        <button
          className={'au-mic' + (mic === 'recording' ? ' recording' : '')}
          disabled={!supported || mic === 'transcribing'}
          title={settings.micMode === 'hold' ? '按住说话' : '点按开始/结束'}
          aria-label="语音输入"
          {...holdHandlers}
        >
          <AuroraOrb size={40} state={orbState} />
        </button>
        <input
          className="au-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(input)}
          placeholder={mic === 'recording' ? '聆听中…' : '发送消息，或说出你的需求…'}
        />
        <button className="au-send" onClick={() => send(input)} aria-label="发送">
          发送
        </button>
      </div>
    </div>
  )
}
