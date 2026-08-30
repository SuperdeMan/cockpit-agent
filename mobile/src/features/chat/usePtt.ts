// PTT（按住说话）状态机（实施计划 M2-2）。
//
// 三个竞态守卫直接对应 HMI MicController 的原账（`hmi/src/audio.ts:53-75` 头注）：
//  ① **快按快松**：start 是异步的，松手可能发生在会话还没建起来时 ⇒ pendingStop 标志，
//     就绪后立刻停。旧实现在这里丢过整段录音。
//  ② **最短时长**：<320ms 当误触，直接取消不定稿——否则每次误触都往后端发一次空识别。
//  ③ **并发按下**：starting/active 期间再次按下直接忽略，一个时刻只有一个采集会话。
//
// barge-in 的 App 版：按下先停播报再开录音，物理上不会自听（计划 M2-3 的 stopTTS 硬停）。
import { useCallback, useEffect, useRef, useState } from 'react'

import { AsrSession } from '@/core/voice/asr'
import { PermissionDeniedError } from '@/core/voice/recorder'
import { speechController } from '@/core/voice/speech'
import { ASR_FALLBACK_MODEL, settingsStore } from '@/core/settings/store'

const MIN_DURATION_MS = 320
// 定稿超过它仍无结果 → UI 给中间反馈。兜底链最长要走 7s（无定稿）+ 7s（换模型）
// + 10s（批处理超时）≈ 24 秒，这段时间一直只写「识别中…」会让人以为卡死了
// ——2026-08-26 真机断网那次**确实**卡死，修完仍有近 30 秒的静默期。
const SLOW_HINT_MS = 8000

export type PttState = 'idle' | 'recording' | 'finalizing'

export interface PttHandle {
  state: PttState
  /** 实时识别文字（上屏，松手后定稿即清空） */
  partial: string
  /** 上一次的失败原因（供 UI 一行提示；下次按下即清） */
  error: string
  /** 失败的**类别**（UI 据此决定给什么救济：去设置授权 / 重试 / 换一句）；空串=没错 */
  errorKind: 'permission' | 'start' | 'asr' | ''
  /** 定稿等太久（兜底链在走）：UI 换一句中间反馈，别让人以为卡死了 */
  slow: boolean
  pressDown(): void
  pressUp(): void
}

export function usePtt(opts: {
  audioUrl: string
  sessionId: string
  onFinal(text: string): void
  /** 稳定 partial（全文）→ 记录里的草稿气泡（方案 §5.2.1） */
  onPartial?(text: string): void
  /** 太短 / 出错 / 空定稿 / 取消 → 草稿不留气泡 */
  onDiscard?(): void
}): PttHandle {
  const [state, setState] = useState<PttState>('idle')
  const [partial, setPartial] = useState('')
  const [error, setError] = useState('')
  const [errorKind, setErrorKind] = useState<PttHandle['errorKind']>('')
  const [slow, setSlow] = useState(false)
  const sessionRef = useRef<AsrSession | null>(null)
  const startingRef = useRef(false)
  const pendingStopRef = useRef(false)
  const startedAtRef = useRef(0)

  const finishSession = useCallback(async () => {
    const session = sessionRef.current
    if (!session) return
    const tooShort = Date.now() - startedAtRef.current < MIN_DURATION_MS
    if (tooShort) {
      sessionRef.current = null
      setState('idle')
      setPartial('')
      opts.onDiscard?.()
      await session.cancel()
      return
    }
    setState('finalizing')
    await session.stop()
  }, [])

  const pressDown = useCallback(() => {
    if (startingRef.current || sessionRef.current) return
    startingRef.current = true
    pendingStopRef.current = false
    setError('')
    setErrorKind('')
    setPartial('')
    setState('recording')
    startedAtRef.current = Date.now()
    speechController().stop() // barge-in：先停播报，再开麦
    const s = settingsStore.getState().settings
    const session = new AsrSession(
      {
        audioUrl: opts.audioUrl,
        language: s.asrLanguage,
        provider: s.asrProvider,
        model: s.asrModel,
        // 选中的就是备用模型时不再指自己（否则失败后原地重试一次，白等一个来回）
        ...(s.asrModel === ASR_FALLBACK_MODEL ? {} : { fallbackModel: ASR_FALLBACK_MODEL }),
        sessionId: opts.sessionId,
      },
      {
        onPartial: (t) => {
          setPartial(t)
          opts.onPartial?.(t)
        },
        onFinal: (t) => {
          sessionRef.current = null
          setPartial('')
          setState('idle')
          const text = t.trim()
          if (text) opts.onFinal(text)
          else {
            opts.onDiscard?.()
            setError('没听清，再说一次？')
            setErrorKind('asr')
          }
        },
        onError: (msg) => {
          sessionRef.current = null
          setPartial('')
          setState('idle')
          opts.onDiscard?.()
          setError(msg)
          setErrorKind('asr')
        },
      },
    )
    sessionRef.current = session
    void session
      .start()
      .then(() => {
        startingRef.current = false
        // ① 会话就绪前就松手了：这时才真正停
        if (pendingStopRef.current) {
          pendingStopRef.current = false
          void finishSession()
        }
      })
      .catch((e: unknown) => {
        startingRef.current = false
        pendingStopRef.current = false
        sessionRef.current = null
        setState('idle')
        opts.onDiscard?.()
        const denied = e instanceof PermissionDeniedError
        setError(denied ? '需要麦克风权限，请在系统设置里允许' : '录音启动失败')
        setErrorKind(denied ? 'permission' : 'start')
      })
  }, [finishSession, opts])

  useEffect(() => {
    if (state !== 'finalizing') {
      setSlow(false)
      return
    }
    const t = setTimeout(() => setSlow(true), SLOW_HINT_MS)
    return () => clearTimeout(t)
  }, [state])

  const pressUp = useCallback(() => {
    if (startingRef.current) {
      pendingStopRef.current = true
      return
    }
    void finishSession()
  }, [finishSession])

  return { state, partial, error, errorKind, slow, pressDown, pressUp }
}
