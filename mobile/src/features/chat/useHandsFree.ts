// 免唤醒回路的 React 接线（实施计划 M4-4）。控制器本身是纯类（core/voice/handsFree.ts），
// 这个 hook 只管三件事：按设置开关它、把 TTS 生命周期喂给它、把它的状态吐给 UI。
//
// **默认关，且原生缺席时连开关都不该出现**——`availability` 就是给 UI 判这个的。
// 判据分开报（vad / kws 各一位）：「不可用」查不出是哪一半最耗时，M3-3 地图那次
// 就是靠分开报一次命中的。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  beginInteraction,
  dropPendingInteraction,
  markInteraction,
  offerPendingInteraction,
} from '@/core/obs/turnTimeline'
import { noteListeningEntered } from '@/core/voice/kwsExperiment'
import { presenceTrail } from '@/core/presence/presenceTrail'
import {
  HandsFreeController,
  handsFreeAvailability,
  type HandsFreeDeps,
} from '@/core/voice/handsFree'
import { speechController } from '@/core/voice/speech'
import { ASR_FALLBACK_MODEL, settingsStore } from '@/core/settings/store'
import type { InteractionScope } from '@/core/session/interactionScope'

export interface UseHandsFreeOpts {
  scope?: InteractionScope
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
  /** turn.end（reason: completed / cancelled / escalated / error…）；不传只清 partial */
  onS2sTurnEnd?(r: { turnId: string; reason: string; detail: string }): void
  /** 逃逸：不传就退回 onSend(utterance)（B1 行为） */
  onS2sEscalated?(utterance: string): void
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
  /** 会话级关闭了语音打断（回声连续自触发）；空串=未关。会话级不可恢复（voiceLoop 语义） */
  bargeInDisabled: string
  /** 本轮语音链路降级说明；下一轮 FSM 进 LISTENING 时清 */
  pipelineDegraded: string
  /** 轻点光球 = 手动唤醒（免唤醒开着时由它开始听） */
  wake(): void
  /** 录音中轻点 = 结束并提交 */
  endUtterance(): void
  /** 只停播（AR03 / 评审 R06）：停当前出声，不发 cancel 帧、不开麦、不开续问窗。免唤醒关着时是 no-op */
  stopSpeaking(): void
  /** 结束本轮收音 / 重新开启插话（评审 D7） */
  recycle(): void
  /** 暂停设备采集；设置不变，下次明确点光球可恢复。 */
  pause(): void
  /** 上一次回声被丢弃的时刻；0=没有 */
  echoAt: number
}

export function useHandsFree(opts: UseHandsFreeOpts): HandsFreeUi {
  const availability = useMemo(() => handsFreeAvailability(), [])
  const [fsm, setFsm] = useState('IDLE')
  const [orb, setOrb] = useState<string | null>(null)
  const [partial, setPartial] = useState('')
  const [error, setError] = useState('')
  const [bargeInDisabled, setBargeInDisabled] = useState('')
  const [pipelineDegraded, setPipelineDegraded] = useState('')
  const [echoAt, setEchoAt] = useState(0)
  /** 免唤醒这一轮的时间线 id 与上一次 FSM 态（AR08）。
   *  用 ref 不用 state：`onOrbState` 是原生回调，读 state 会读到上一次渲染的那份。 */
  const hfTimelineRef = useRef<string | null>(null)
  const prevFsmRef = useRef('')
  const ctlRef = useRef<HandsFreeController | null>(null)
  const pausedRef = useRef(false)
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
    if (!wantOn || !settingsStore.getState().settings.handsFree) return
    pausedRef.current = false
    let live = true
    const allowed = () => live && ctlRef.current === ctl && !pausedRef.current &&
      (!opts.scope || opts.scope.canCapture()) && settingsStore.getState().settings.handsFree
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
        if (!allowed()) return
        // 走到这里 = ASR 已定稿并交给主链；下一步 SessionCore.send 会认领这一轮
        if (hfTimelineRef.current) {
          markInteraction(hfTimelineRef.current, 'asr_final', { detail: text.trim() ? 'text' : 'empty' })
          offerPendingInteraction(hfTimelineRef.current)
        }
        setPartial('')
        // **与文本、与 PTT 完全同一条 send 路径**：前置路由/位置闸/候选拦截一条都不能
        // 因为「这句是免唤醒说出来的」而绕过（同 M2 那条判据）
        cbRef.current.onSend(text)
      },
      onStopTts: () => speechController().stop(),
      onOrbState: (o, f) => {
        if (!live) return
        // 进入 LISTENING = 唤醒命中且开始收音：这一轮的计时从这里起（AR08）。
        // 离开 LISTENING 而没发出去（噪声句 / 用户放弃）⇒ 把交接口收回来，
        // 否则下一条**文字**请求会认领到一个语音轮，凭空多出一段「说话」。
        if (f !== prevFsmRef.current) {
          if (f === 'LISTENING') {
            noteListeningEntered() // AR07：“真的进了可交互态”那一半（与原生命中分开计）
            const t = beginInteraction('handsfree')
            hfTimelineRef.current = t
            markInteraction(t, 'input_gesture', { detail: 'wake' })
            markInteraction(t, 'capture_started')
            offerPendingInteraction(t)
          } else if (prevFsmRef.current === 'LISTENING' && hfTimelineRef.current) {
            dropPendingInteraction(hfTimelineRef.current)
          }
          prevFsmRef.current = f
        }
        // §11.4「首反馈时延」的取数源：这里是 FSM 换态的**回调时刻**（≈KWS 命中），
        // 与随后第一条 primary=listening 的轨迹快照之差 = 屏上多久才有反应（B2 T14）
        presenceTrail.mark('fsm:' + f)
        setOrb(o)
        setFsm(f)
        if (f !== 'LISTENING') setPartial('')
        // 降级是**本轮**的事实：下一轮真的开始听了就该消失，否则那行字会一直挂着
        if (f === 'LISTENING') setPipelineDegraded('')
      },
      onPartialText: (t) => {
        if (!allowed()) return
        setPartial(t)
        cbRef.current.onPartial?.(t)
      },
      onCancelTurn: () => { if (allowed()) cbRef.current.onCancelTurn?.() },
      onNotice: (m) => cbRef.current.onNotice?.(m),
      onBargeInDisabled: (r) => setBargeInDisabled(r),
      onEchoDismissed: () => setEchoAt(Date.now()),
      onPipelineDegraded: (_k, m) => setPipelineDegraded(m),
      wakeWord: () => settingsStore.getState().settings.wakeWord,
      getVoicePipeline: () => settingsStore.getState().settings.voicePipeline,
      // S2S 的 provider / 音色**不是** TTS 的那两个（值域不同：网关 S2S provider 只认
      // dashscope / mock / off，`llm-gateway/s2s/provider.py:388-419`）。B1 这里填的是
      // `ttsProvider='minimax'` + `voiceId='female-shaonv'` ⇒ `build_s2s_provider` 一路
      // `return None`，网关每次 session.start 都回 unsupported「S2S 未配置或无凭据」，
      // 端到端一轮从来没走通过（B2 T5 真机取证定位）。两个都不给＝走网关 env 缺省，
      // 与 HMI 同（HMI 给的 voice 是它自己的 `s2sVoice` 设置项，App 没有这一项）。
      getS2sConfig: () => ({}),
      getSessionMeta: () => ({ sessionId: cbRef.current.sessionId }),
      onS2sUserUtterance: (t) => { if (allowed()) cbRef.current.onS2sUserUtterance?.(t) },
      onS2sAnswerDelta: (t) => { if (allowed()) cbRef.current.onS2sAnswerDelta?.(t) },
      onS2sEscalated: (utterance) => {
        if (allowed()) (cbRef.current.onS2sEscalated ?? cbRef.current.onSend)(utterance)
      },
      onS2sTurnEnd: (r) => {
        if (!allowed()) return
        setPartial('')
        cbRef.current.onS2sTurnEnd?.(r)
      },
    }
    const ctl = new HandsFreeController(deps)
    ctlRef.current = ctl
    const onEnableError = (e: unknown) => {
      if (!allowed()) return
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      cbRef.current.onNotice?.('免唤醒启动失败：' + msg)
    }
    // Zustand 通知同步发生：设置关掉的同一调用栈就撤回采集，不能等 React effect。
    const unsubscribeSettings = settingsStore.subscribe((state, previous) => {
      if (state.settings.handsFree === previous.settings.handsFree || !live) return
      if (!state.settings.handsFree) void ctl.disable().catch(() => {})
      else if (cbRef.current.enabled && (!opts.scope || opts.scope.canCapture())) {
        pausedRef.current = false
        void ctl.enable().catch(onEnableError)
      }
    })
    const syncScope = () => {
      if (opts.scope && !opts.scope.canCapture()) void ctl.disable().catch(() => {})
      else if (!pausedRef.current && settingsStore.getState().settings.handsFree) void ctl.enable().catch(onEnableError)
    }
    const unsubscribeScope = opts.scope?.subscribe(syncScope)
    syncScope()

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
    sc.onSilent = (reason, kind) => {
      prevSilent?.(reason, kind)
      // FSM 这条腿与「该不该提示用户」无关：不管哪种成因，这一轮的播报都结束了。
      ctl.turnEnded()
    }
    return () => {
      live = false
      unsubscribeSettings()
      unsubscribeScope?.()
      sc.onSpeechBegan = prevBegan
      sc.onSpeechText = prevText
      sc.onSpeechEnded = prevEnded
      sc.onSilent = prevSilent
      ctlRef.current = null
      void ctl.dispose().catch(() => {})
      setOrb(null)
      setFsm('IDLE')
      setPartial('')
      setBargeInDisabled('')
      setPipelineDegraded('')
      setEchoAt(0)
      // 上一条启动失败的话不能留给下一个控制器：清理与其它五个状态同一处（放这里而不是
      // 新 effect 体里同步 setState —— 那是 react-hooks/set-state-in-effect 的级联渲染）
      setError('')
    }
  }, [wantOn, opts.audioUrl, opts.sessionId, opts.scope])

  // 挂起确认镜像：单独一个 effect，跟着 needConfirm 变（不进控制器重建的依赖）
  const needConfirm = !!opts.needConfirm
  useEffect(() => {
    ctlRef.current?.setNeedConfirm(needConfirm)
  }, [needConfirm, wantOn])

  const wake = useCallback(() => {
    if (cbRef.current.scope && !cbRef.current.scope.canCapture()) return
    const ctl = ctlRef.current
    if (!ctl) return
    if (pausedRef.current) {
      pausedRef.current = false
      void ctl.enable().then(() => {
        if (ctlRef.current === ctl && !pausedRef.current && (!cbRef.current.scope || cbRef.current.scope.canCapture())) ctl.wakeManually()
      }).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
    } else ctl.wakeManually()
  }, [])
  const endUtterance = useCallback(() => ctlRef.current?.endUtterance(), [])
  const recycle = useCallback(() => ctlRef.current?.recycle(), [])
  const stopSpeaking = useCallback(() => ctlRef.current?.stopSpeaking(), [])
  const pause = useCallback(() => {
    pausedRef.current = true
    void ctlRef.current?.disable().catch(() => {})
  }, [])
  return { fsm, orb, partial, availability, error, bargeInDisabled, pipelineDegraded, wake, endUtterance, recycle, stopSpeaking, pause, echoAt }
}
