// 底部输入区：快捷指令轨 + 麦克风 + 文本输入。
// 麦克风支持"按住说话/点按切换"两种模式，复用 MicController 消除收音竞态。
import { useEffect, useRef, useState } from 'react'
import { useSettings } from '../settings'
import { MicController, micSupported, secureContextOk, recognize, type RecordResult } from '../audio'

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

  const beginRecord = async () => {
    if (!supported || mic !== 'idle') return
    setNotice('')
    try {
      setMic('recording')
      await ctrlRef.current!.start(settings.listenSeconds * 1000, onResult)
    } catch {
      setMic('idle')
      setNotice('无法访问麦克风，请检查权限')
    }
  }

  const endRecord = () => {
    if (mic === 'recording') ctrlRef.current!.stop()
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

  const micLabel =
    mic === 'recording' ? '🔴' : mic === 'transcribing' ? '◌' : '🎤'

  return (
    <div className="composer-wrap">
      <div className="quick-rail">
        {settings.quickCommands.map((q) => (
          <button key={q} className="quick-chip" onClick={() => send(q)}>
            {q}
          </button>
        ))}
      </div>

      {(notice || hint) && <div className="composer-notice">{notice || hint}</div>}

      <div className="composer">
        <button
          className={'mic' + (mic === 'recording' ? ' recording' : '') + (mic === 'transcribing' ? ' busy' : '')}
          disabled={!supported || mic === 'transcribing'}
          title={settings.micMode === 'hold' ? '按住说话' : '点按开始/结束'}
          aria-label="语音输入"
          {...holdHandlers}
        >
          <span className="mic-glyph">{micLabel}</span>
          {mic === 'recording' && <span className="mic-wave"><i /><i /><i /><i /><i /></span>}
        </button>
        <input
          className="composer-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(input)}
          placeholder={mic === 'recording' ? '聆听中…' : '输入指令或按住麦克风说话…'}
        />
        <button className="send-btn" onClick={() => send(input)} aria-label="发送">
          发送
        </button>
      </div>
    </div>
  )
}
