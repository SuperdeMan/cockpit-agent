// 免唤醒回路的 React 接线（实施计划 M4-4）。控制器本身是纯类（core/voice/handsFree.ts），
// 这个 hook 只管三件事：按设置开关它、把 TTS 生命周期喂给它、把它的状态吐给 UI。
//
// **默认关，且原生缺席时连开关都不该出现**——`availability` 就是给 UI 判这个的。
// 判据分开报（vad / kws 各一位）：「不可用」查不出是哪一半最耗时，M3-3 地图那次
// 就是靠分开报一次命中的。
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  HandsFreeController,
  handsFreeAvailability,
  type HandsFreeDeps,
} from '@/core/voice/handsFree'
import { speechController } from '@/core/voice/speech'
import { ASR_FALLBACK_MODEL, settingsStore } from '@/core/settings/store'

export interface UseHandsFreeOpts {
  audioUrl: string
  sessionId: string
  /** 设置里开没开（用户显式打开才常开麦——那是采集面） */
  enabled: boolean
  onSend(text: string, metaExtra?: Record<string, string>): void
  onPartial?(text: string): void
  onNotice?(msg: string): void
  onCancelTurn?(): void
  /** S2S 自答轮的用户话/回答增量（classic 挡位下不会触发） */
  onS2sUserUtterance?(text: string): void
  onS2sAnswerDelta?(text: string): void
  /** 主链有待确认动作（危险车控）的镜像。FSM 据此**不把「确认/取消」本地消化掉**
   *  ——那两个字必须上主链走 require_confirm 闸（红线）。 */
  needConfirm?: boolean
}

export interface HandsFreeUi {
  /** FSM 态：IDLE/ARMED/LISTENING/THINKING/SPEAKING/FOLLOWUP */
  fsm: string
  /** 光球视觉态（null=回 IDLE，麦态交还 PTT） */
  orb: string | null
  partial: string
  availability: { vad: boolean; kws: boolean; usable: boolean }
  /** 载模型/开麦失败时的原因（UI 据此把开关自动弹回并解释） */
  error: string
}

export function useHandsFree(opts: UseHandsFreeOpts): HandsFreeUi {
  const availability = useMemo(() => handsFreeAvailability(), [])
  const [fsm, setFsm] = useState('IDLE')
  const [orb, setOrb] = useState<string | null>(null)
  const [partial, setPartial] = useState('')
  const [error, setError] = useState('')
  const ctlRef = useRef<HandsFreeController | null>(null)
  // 回调用 ref 存：它们每次渲染都是新函数，进依赖会让控制器反复重建（=反复开关麦）。
  // **在 effect 里更新而不是渲染期直接赋值**：渲染期写 ref 违反 React 的纯度约束
  // （react-hooks/refs），且并发渲染下会写到被丢弃的那次渲染上。
  // 首渲染由 useRef(opts) 的初值兜住；后续由下面这个 effect 更新，而它声明在控制器
  // effect **之前**，所以控制器建起来时读到的一定是最新一次的回调。
  const cbRef = useRef(opts)
  useEffect(() => {
    cbRef.current = opts
  })

  const wantOn = opts.enabled && availability.usable

  useEffect(() => {
    if (!wantOn) return
    const deps: HandsFreeDeps = {
      audioUrl: cbRef.current.audioUrl,
      getSessionId: () => cbRef.current.sessionId,
      getAsrConfig: () => {
        const s = settingsStore.getState().settings
        return {
          language: s.asrLanguage,
          provider: s.asrProvider,
          model: s.asrModel,
          fallbackModel: ASR_FALLBACK_MODEL,
        }
      },
      onSend: (text) => {
        setPartial('')
        // **与文本、与 PTT 完全同一条 send 路径**：前置路由/位置闸/候选拦截一条都不能
        // 因为「这句是免唤醒说出来的」而绕过（同 M2 那条判据）
        cbRef.current.onSend(text)
      },
      onStopTts: () => speechController().stop(),
      onOrbState: (o, f) => {
        setOrb(o)
        setFsm(f)
        if (f !== 'LISTENING') setPartial('')
      },
      onPartialText: (t) => {
        setPartial(t)
        cbRef.current.onPartial?.(t)
      },
      onCancelTurn: () => cbRef.current.onCancelTurn?.(),
      onNotice: (m) => cbRef.current.onNotice?.(m),
      wakeWord: () => settingsStore.getState().settings.wakeWord,
      getVoicePipeline: () => settingsStore.getState().settings.voicePipeline,
      getS2sConfig: () => {
        const s = settingsStore.getState().settings
        return { voice: s.voiceId, provider: s.ttsProvider }
      },
      getSessionMeta: () => ({ sessionId: cbRef.current.sessionId }),
      onS2sUserUtterance: (t) => cbRef.current.onS2sUserUtterance?.(t),
      onS2sAnswerDelta: (t) => cbRef.current.onS2sAnswerDelta?.(t),
      onS2sEscalated: (utterance) => cbRef.current.onSend(utterance),
      onS2sTurnEnd: () => setPartial(''),
    }
    const ctl = new HandsFreeController(deps)
    ctlRef.current = ctl
    setError('')
    ctl.enable().catch((e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      cbRef.current.onNotice?.('免唤醒启动失败：' + msg)
    })

    // TTS 三条腿接到 FSM：出声 → SPEAKING；播完 → FOLLOWUP；**没出声也要收尾**
    // （引擎无 key / 纯卡片回复时一个字节都不出，不补这一脚 FSM 会卡在 THINKING
    //  直到 100s 兜底，那段时间整个回路是聋的——HMI R4.3b P0 的原账）
    const sc = speechController()
    const prevBegan = sc.onSpeechBegan
    const prevText = sc.onSpeechText
    const prevEnded = sc.onSpeechEnded
    const prevSilent = sc.onSilent
    sc.onSpeechBegan = (text) => {
      prevBegan?.(text)
      ctl.ttsStart(text)
    }
    // 播报文本随流式变长 → 持续喂给 FSM。**回声防线的输入就是这一条**：
    // `onSpeechBegan` 挂在首片音频上，那一刻文本还没累积起来（真机实测 len=0）。
    sc.onSpeechText = (text) => {
      prevText?.(text)
      ctl.setTtsText(text)
    }
    sc.onSpeechEnded = () => {
      prevEnded?.()
      ctl.ttsEnd()
    }
    sc.onSilent = (reason) => {
      prevSilent?.(reason)
      ctl.turnEnded()
    }
    return () => {
      sc.onSpeechBegan = prevBegan
      sc.onSpeechText = prevText
      sc.onSpeechEnded = prevEnded
      sc.onSilent = prevSilent
      ctlRef.current = null
      void ctl.dispose()
      setOrb(null)
      setFsm('IDLE')
      setPartial('')
    }
  }, [wantOn])

  // 挂起确认镜像：单独一个 effect，跟着 needConfirm 变（不进控制器重建的依赖）
  const needConfirm = !!opts.needConfirm
  useEffect(() => {
    ctlRef.current?.setNeedConfirm(needConfirm)
  }, [needConfirm, wantOn])

  return { fsm, orb, partial, availability, error }
}
