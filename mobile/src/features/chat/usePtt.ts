// PTT 状态机（实施计划 M2-2）+ B2 T6 的手势契约入口（方案 §5.1.1）：
//  · 按住 / 松手（hold）：三个竞态守卫照旧（快按快松 pendingStop / <320ms 误触 / 并发按下忽略）
//  · 轻点（tap）：开始录音、说完自动收尾（core/voice/tapTalk.ts 三层收尾）；录音中再点 = 结束并提交
//  · 取消（cancel）：按住时上滑 / 隐私栏「关闭本轮麦克风」——不定稿、草稿删除；胶囊说
//    「已取消，这段话不会发给小舟」（**不是**「未上传」：流式 ASR 已上传的音频不能宣称从未上传）
//
// 麦：AsrSession 领一路 micLease（§0 第 5 条：全局单例的那只录音机在免唤醒开着时让 PTT 收不到帧、
// 且它的 stop 会把免唤醒的真麦停掉——pttLease.test.ts 钉住）。
// barge-in 的 App 版：按下先停播报再开麦，物理上不会自听（计划 M2-3 的 stopTTS 硬停）。
import { useCallback, useEffect, useRef, useState } from 'react'

import { AsrSession, type AsrCallbacks, type AsrConfig } from '@/core/voice/asr'
import { micLease } from '@/core/voice/micBus'
import { PermissionDeniedError } from '@/core/voice/recorder'
import { speechController } from '@/core/voice/speech'
import { TapTalkSession, vadEndpoint } from '@/core/voice/tapTalk'
import { ASR_FALLBACK_MODEL, settingsStore } from '@/core/settings/store'

const MIN_DURATION_MS = 320
// 定稿超过它仍无结果 → UI 给中间反馈（兜底链最长 ≈24s，一直只写「识别中…」会让人以为卡死）
const SLOW_HINT_MS = 8000

export type PttState = 'idle' | 'recording' | 'finalizing'
export type PttMode = 'hold' | 'tap' | ''

export interface PttHandle {
  state: PttState
  /** 这次录音是按住还是轻点（空=没在录） */
  mode: PttMode
  partial: string
  error: string
  errorKind: 'permission' | 'start' | 'asr' | ''
  slow: boolean
  /** 上一次取消的时刻（胶囊短显「已取消…」用）；0=没有 */
  cancelledAt: number
  pressDown(): void
  pressUp(): void
  /** 轻点契约：空闲 = 开始录音（自动收尾）；录音中 = 结束并提交；识别中 = 忽略 */
  tap(): void
  /** 取消本次录音：不定稿、不回调 onFinal、草稿删除 */
  cancel(): void
}

interface VoiceSession {
  start(): Promise<void>
  stop(): Promise<void>
  cancel(): Promise<void>
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
  const [mode, setMode] = useState<PttMode>('')
  const [partial, setPartial] = useState('')
  const [error, setError] = useState('')
  const [errorKind, setErrorKind] = useState<PttHandle['errorKind']>('')
  const [slow, setSlow] = useState(false)
  const [cancelledAt, setCancelledAt] = useState(0)
  const sessionRef = useRef<VoiceSession | null>(null)
  const startingRef = useRef(false)
  const pendingStopRef = useRef(false)
  const pendingCancelRef = useRef(false)
  const startedAtRef = useRef(0)

  const reset = useCallback(() => {
    sessionRef.current = null
    startingRef.current = false
    pendingStopRef.current = false
    pendingCancelRef.current = false
    setPartial('')
    setState('idle')
    setMode('')
  }, [])

  const asrConfig = useCallback((): AsrConfig => {
    const s = settingsStore.getState().settings
    return {
      audioUrl: opts.audioUrl,
      language: s.asrLanguage,
      provider: s.asrProvider,
      model: s.asrModel,
      // 选中的就是备用模型时不再指自己（否则失败后原地重试一次，白等一个来回）
      ...(s.asrModel === ASR_FALLBACK_MODEL ? {} : { fallbackModel: ASR_FALLBACK_MODEL }),
      sessionId: opts.sessionId,
    }
  }, [opts.audioUrl, opts.sessionId])

  const callbacks = useCallback(
    (): AsrCallbacks => ({
      onPartial: (t) => {
        setPartial(t)
        opts.onPartial?.(t)
      },
      onFinal: (t) => {
        reset()
        const text = t.trim()
        if (text) opts.onFinal(text)
        else {
          setError('没听清，再说一次？')
          setErrorKind('asr')
          opts.onDiscard?.()
        }
      },
      onError: (msg) => {
        reset()
        setError(msg)
        setErrorKind('asr')
        opts.onDiscard?.()
      },
    }),
    [opts, reset],
  )

  const finishSession = useCallback(async () => {
    const session = sessionRef.current
    if (!session) return
    if (Date.now() - startedAtRef.current < MIN_DURATION_MS) {
      // ② 误触：不定稿——否则每次误触都往后端发一次空识别
      reset()
      opts.onDiscard?.()
      await session.cancel()
      return
    }
    setState('finalizing')
    await session.stop()
  }, [opts, reset])

  const begin = useCallback(
    (kind: 'hold' | 'tap') => {
      if (startingRef.current || sessionRef.current) return // ③ 并发按下忽略
      startingRef.current = true
      pendingStopRef.current = false
      pendingCancelRef.current = false
      setError('')
      setErrorKind('')
      setPartial('')
      setState('recording')
      setMode(kind)
      startedAtRef.current = Date.now()
      speechController().stop() // barge-in：先停播报，再开麦
      const session: VoiceSession =
        kind === 'tap'
          ? new TapTalkSession(asrConfig(), callbacks(), { endpoint: vadEndpoint() })
          : new AsrSession(asrConfig(), callbacks(), micLease())
      sessionRef.current = session
      void session
        .start()
        .then(() => {
          startingRef.current = false
          if (pendingCancelRef.current) {
            pendingCancelRef.current = false
            void session.cancel()
            return
          }
          if (pendingStopRef.current) {
            // ① 会话就绪前就松手了：这时才真正停
            pendingStopRef.current = false
            void finishSession()
          }
        })
        .catch((e: unknown) => {
          reset()
          const denied = e instanceof PermissionDeniedError
          setError(denied ? '需要麦克风权限，请在系统设置里允许' : '录音启动失败')
          setErrorKind(denied ? 'permission' : 'start')
          opts.onDiscard?.()
        })
    },
    [asrConfig, callbacks, finishSession, opts, reset],
  )

  useEffect(() => {
    if (state !== 'finalizing') {
      setSlow(false)
      return
    }
    const t = setTimeout(() => setSlow(true), SLOW_HINT_MS)
    return () => clearTimeout(t)
  }, [state])

  const pressDown = useCallback(() => begin('hold'), [begin])

  const pressUp = useCallback(() => {
    if (startingRef.current) {
      pendingStopRef.current = true
      return
    }
    void finishSession()
  }, [finishSession])

  const tap = useCallback(() => {
    if (state === 'finalizing') return
    if (sessionRef.current || startingRef.current) {
      pressUp() // 录音中轻点 = 结束并提交
      return
    }
    begin('tap')
  }, [begin, pressUp, state])

  const cancel = useCallback(() => {
    if (!sessionRef.current && !startingRef.current) return
    if (startingRef.current) {
      pendingCancelRef.current = true // 会话还没建起来：就绪后立刻 cancel，别留一个开着的麦
      setState('idle')
      setMode('')
      setPartial('')
    } else {
      const session = sessionRef.current
      reset()
      void session?.cancel()
    }
    setCancelledAt(Date.now())
    opts.onDiscard?.()
  }, [opts, reset])

  return { state, mode, partial, error, errorKind, slow, cancelledAt, pressDown, pressUp, tap, cancel }
}
