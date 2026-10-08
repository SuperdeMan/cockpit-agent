// 底部输入区（Aurora Glass）：快捷指令轨 + 小舟光球 + 麦克风 + 文本输入 + 发送。
// 语音输入两条路：①流式实时上屏（StreamingRecognizer→WS，partial 写进输入框）；
// ②批处理（MicController→recognize，录完再出）。流式失败本会话无感回退批处理。
import { useEffect, useRef, useState } from 'react'
import { useSettings } from '../settings'
import {
  MicController, micSupported, secureContextOk, recognize, stopTTS,
  StreamingRecognizer, streamingAsrSupported, asrStreamUrl, type RecordResult,
} from '../audio'
import { AuroraOrb, type OrbState } from './aurora'
import { Icon } from './Icon'
import { useDriving } from '../DrivingContext'

type MicState = 'idle' | 'recording' | 'transcribing'

export function Composer({
  audioApi,
  onSend,
  hint,
  handsFreeOrb,
  onWake,
  busy = false, speaking = false, onStop, onPartial, onActivity, drivingAnswer, drivingSource,
}: {
  audioApi: string
  onSend: (text: string) => void
  hint?: string
  handsFreeOrb?: string | null // R4.3：hands-free 激活时 FSM 的 orb 态（armed/listening/…），覆盖空闲 mic 态
  onWake?: () => void // R4.3：hands-free 激活时点光球=开启聆听（VAD-only 的「一次点击开启」）
  busy?: boolean
  speaking?: boolean
  onStop?: () => void
  onPartial?: (text: string) => void
  onActivity?: (activity: { mic: boolean; cloud: boolean }) => void
  drivingAnswer?: string
  drivingSource?: 'speech' | 'answer'
}) {
  const { settings } = useSettings()
  const { driving } = useDriving()
  const [input, setInput] = useState('')
  const [mic, setMic] = useState<MicState>('idle')
  const [notice, setNotice] = useState<string>('')
  const ctrlRef = useRef<MicController | null>(null)
  if (!ctrlRef.current) ctrlRef.current = new MicController()
  const streamRef = useRef<StreamingRecognizer | null>(null)
  if (!streamRef.current) streamRef.current = new StreamingRecognizer()
  // 流式模式：能力支持即走 WS（整句引擎也经同一条 WS，网关攒段后出字）；一旦流式失败则本会话回退批处理。
  // 「关闭=录完再识别」那一档 2026-09-14 退役：它和整句体验一致，批处理只作回退路径。
  const streamModeRef = useRef(streamingAsrSupported())
  useEffect(() => {
    streamModeRef.current = streamingAsrSupported()
  }, [settings.asrProvider])
  useEffect(() => {
    onActivity?.({ mic: mic === 'recording', cloud: mic === 'transcribing' || (mic === 'recording' && streamModeRef.current) })
    if (mic === 'idle') onPartial?.('')
  }, [mic, onActivity, onPartial])

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
    // (provider, model) 成对存储（settings.load 自愈过），整句引擎的 model 也照传（minimax asr-1.0 / mimo）
    await streamRef.current!.start(asrStreamUrl(audioApi), {
      language: settings.asrLanguage,
      provider: settings.asrProvider,
      model: settings.asrModel,
      onPartial: (t) => onPartial?.(t),
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

  // hands-free 激活时（无唤醒词的 VAD-only）：点光球=开启/续接聆听（vl.wake）；VAD 负责断句
  const handsFreeActive = !!handsFreeOrb && handsFreeOrb !== 'idle'
  // 按住说话：press/release；点按切换：click 切换
  const holdHandlers = handsFreeActive
    ? { onClick: () => { if (mic === 'idle') onWake?.() } }
    : settings.micMode === 'hold'
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

  // 语音按钮即小舟光球：录音→speaking（波纹）、识别中→thinking（律动）；
  // 空闲时若 hands-free 激活则显 FSM 态（armed 待机微光 / listening 聆听脉冲 / …），否则 idle 呼吸。
  const orbState: OrbState =
    mic === 'recording' ? 'listening' : mic === 'transcribing' ? 'thinking'
    : speaking ? 'speaking' : busy ? 'thinking' : ((handsFreeOrb as OrbState) || 'idle')
  const showStop = !input.trim() && (busy || speaking)

  return (
    <div className="au-composer">
      {!driving && <div className="au-quick-rail">
        {settings.quickCommands.slice(0, 4).map((q) => (
          <button key={q} className="au-quick-chip" onClick={() => send(q)}>
            {q}
          </button>
        ))}
      </div>}

      {(notice || hint) && <div className="au-composer-notice">{notice || hint}</div>}

      <div className="au-composer-bar">
        <button
          className={'au-mic' + (mic === 'recording' ? ' recording' : '')}
          disabled={!supported || mic === 'transcribing'}
          title={settings.micMode === 'hold' ? '按住说话' : '点按开始/结束'}
          aria-label="语音输入"
          {...holdHandlers}
        >
          <AuroraOrb size={driving ? 96 : 72} state={orbState} driving={driving} />
        </button>
        {driving ? <div className="au-driving-answer" data-source={drivingSource}
          aria-label={drivingSource === 'speech' ? '播报内容' : '回答内容'}>{drivingAnswer}</div> : <input
          className="au-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send(input)}
          placeholder={mic === 'recording' ? (settings.micMode === 'hold' ? '正在听… 松开结束' : '正在听… 再点光球结束')
            : mic === 'transcribing' ? '正在识别…' : handsFreeOrb === 'listening' ? '正在听… 停顿即自动发送'
            : speaking ? '正在播报，点光球可打断' : busy ? '正在思考…'
            : handsFreeOrb === 'armed' ? '免唤醒已开启，直接说出需求'
            : settings.micMode === 'hold' ? '输入文字，或按住光球说话' : '输入文字，或点光球说话'}
        />}
        {!driving && <button className={'au-send' + (showStop ? ' stop' : '')}
          disabled={!busy && !speaking && !input.trim()}
          onClick={() => showStop ? onStop?.() : send(input)} aria-label={showStop ? '停止' : '发送'}>
          <Icon name={showStop ? 'stop' : 'arrowUp'} size={28} color="currentColor" />
        </button>}
      </div>
    </div>
  )
}
