// AR04：应用内唯一语音宿主；页面只消费同一份控制器与会话事实。
import { router, usePathname } from 'expo-router'
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { AppState, BackHandler, Keyboard, View, useWindowDimensions } from 'react-native'
import { useStore } from 'zustand'

import { fetchSessionInfo } from '@/core/api/sessionInfo'
import { loadServerConfig, subscribeServerConfig } from '@/core/config/storage'
import type { ServerConfig } from '@/core/config/types'
import { InteractionScope } from '@/core/session/interactionScope'
import { disposeWired, ensureWired, type Wired } from '@/core/session/wiring'
import type { IssueView, RecoveryKind } from '@/core/session/contracts'
import type { SendOpts } from '@/core/session/store'
import { currentTurn } from '@/core/session/turnView'
import { settingsStore } from '@/core/settings/store'
import { activityLog } from '@/core/presence/activityLog'
import { sheetResident } from '@/core/presence/drivingMode'
import { useReduceMotion } from '@/core/a11y/reduceMotion'
import { speechController } from '@/core/voice/speech'
import { canStopPlayback, stopPlayback } from '@/core/voice/stopPlayback'
import { getAudioPlaybackSnapshot, subscribeAudioPlayback } from '@/core/voice/playbackFacts'
import { cancelVisionCapture, captureVisionFrame, needsVisionFrame, visionCapabilitySignal } from '@/core/vision/frame'
import { useHandsFree } from '@/features/chat/useHandsFree'
import { usePresence, type SheetOverride } from '@/features/chat/usePresence'
import { usePtt } from '@/features/chat/usePtt'
import { PrivacyRail } from '@/features/chat/PrivacyRail'
import { VisionCapture } from '@/features/vision/VisionCapture'
import { useLayout } from '@/ui/layout/useLayout'
import { screenSwitch } from '@/ui/layout/sizeClass'
import { usePalette } from '@/ui/theme'

type Connection = { wired: Wired; cfg: ServerConfig }
const AssistantContext = createContext<AssistantRuntime | null>(null)
export function useAssistant(): AssistantRuntime | null { return useContext(AssistantContext) }

export function AssistantProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const [scope] = useState(() => new InteractionScope({
    route: pathname, foreground: AppState.currentState === 'active', focused: true, configured: false, keyboardVisible: Keyboard.isVisible(),
  }))
  const [connection, setConnection] = useState<Connection | null | undefined>(undefined)
  useLayoutEffect(() => { scope.update({ route: pathname }) }, [scope, pathname])
  useEffect(() => {
    const change = AppState.addEventListener('change', (state) => scope.update({ foreground: state === 'active' }))
    const blur = AppState.addEventListener('blur', () => scope.update({ focused: false }))
    const focus = AppState.addEventListener('focus', () => scope.update({ focused: true }))
    const keyboardShow = Keyboard.addListener('keyboardDidShow', () => scope.update({ keyboardVisible: true }))
    const keyboardHide = Keyboard.addListener('keyboardDidHide', () => scope.update({ keyboardVisible: false }))
    return () => { change.remove(); blur.remove(); focus.remove(); keyboardShow.remove(); keyboardHide.remove(); scope.update({ foreground: false }) }
  }, [scope])
  useEffect(() => {
    let generation = 0
    let live = true
    const refresh = () => {
      const current = ++generation
      scope.update({ configured: false })
      void loadServerConfig().then((cfg) => {
        if (!live || generation !== current) return
        if (!cfg) { disposeWired(); setConnection(null); return }
        setConnection({ cfg, wired: ensureWired(cfg) })
      })
    }
    const unsubscribe = subscribeServerConfig(refresh)
    refresh()
    return () => { live = false; unsubscribe(); scope.update({ configured: false }); disposeWired() }
  }, [scope])
  // 配置装载期间不挂会主动导航的页面；Stack 一旦挂载，页间切换不会重建此宿主。
  if (connection === undefined) return <View style={{ flex: 1 }} />
  if (!connection) return <AssistantContext.Provider value={null}>{children}</AssistantContext.Provider>
  return <ReadyAssistant key={connection.wired.session.sessionId} {...connection} scope={scope}>{children}</ReadyAssistant>
}

function useAssistantRuntime({ wired, cfg, scope }: Connection & { scope: InteractionScope }) {
  const { core } = wired
  const state = useStore(core.store)
  const { messages, pendingOps, pendingLocationText } = state
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const facts = useSyncExternalStore(scope.subscribe, scope.snapshot)
  const [notice, setNotice] = useState('')
  const [sheetOverride, setSheetOverride] = useState<SheetOverride | null>(null)
  const [privacyOpen, setPrivacyOpen] = useState(false)
  const [dockOpenedIds, setDockOpenedIds] = useState<string[]>([])
  const [draft, setDraft] = useState('')
  const turn = useMemo(() => currentTurn(messages), [messages])
  const latestTurnId = turn.assistant?.id ?? ''
  const onSend = useCallback((text: string, metaExtra?: Record<string, string>, opts?: SendOpts) => {
    if (!scope.canCapture()) return false
    if (settings.visionEnabled && !(metaExtra && 'vision_frame_id' in metaExtra) && needsVisionFrame(text)) {
      core.send(text, metaExtra, {
        ...opts, preparationSignal: visionCapabilitySignal(),
        prepareMeta: async (bubbleId, signal) => {
          const frameId = await captureVisionFrame(cfg.audioUrl, signal)
          if (frameId) core.markVision(bubbleId)
          return { vision_frame_id: frameId }
        },
      })
    } else core.send(text, metaExtra, opts)
    return true
  }, [core, cfg.audioUrl, settings.visionEnabled, scope])
  const onConfirm = useCallback((reply: '确认' | '取消', operationId?: string) => {
    if (!scope.canCapture()) return
    if (!operationId && pendingLocationText !== null && reply === '确认') activityLog.push('location', '位置授权 · 同意')
    core.confirmReply(reply, operationId)
  }, [core, pendingLocationText, scope])
  // 显式补槽回复（AR05 §4.2）：与 onConfirm 同一条采集域闸；不经普通发送路由，
  // 免得点了建议值却被上一轮候选或定位征询截走。
  const onSlotReply = useCallback((operationId: string, value: string) => {
    if (!scope.canCapture()) return
    core.slotReply(operationId, value)
  }, [core, scope])
  // 结构化问题的恢复出口（AR05 §5.1）。**只做客户端真能兑现的事**：
  //  · 三个"去某处配置"跳设置页——业务授权不足绝不指向系统权限页（V07）；
  //  · retry_request **只把原话放回输入框，不自动重发**——本轮可能已经执行了一部分，
  //    或者结果未知，自动整轮重发会把一件事做两次（§5.1）。
  const onIssueAction = useCallback((kind: RecoveryKind, issue: IssueView) => {
    if (kind === 'dismiss') {
      core.dismissIssue(issue.code, issue.operationId)
      return
    }
    if (kind === 'retry_request') {
      if (issue.retryText) setDraft(issue.retryText)
      core.dismissIssue(issue.code, issue.operationId)
      return
    }
    router.push('/settings')
  }, [core, setDraft])
  const onInterrupt = useCallback(() => { if (scope.canCapture()) core.cancelCurrentTurn() }, [core, scope])
  const ptt = usePtt({
    audioUrl: cfg.audioUrl, sessionId: wired.session.sessionId, scope,
    onPartial: (t) => core.draftUser(t), onDiscard: () => core.discardDraftUser(),
    onFinal: (text) => onSend(text, undefined, { source: 'ptt', bubbleId: core.commitDraftUser() ?? undefined }),
  })
  const hf = useHandsFree({
    audioUrl: cfg.audioUrl, sessionId: wired.session.sessionId, scope,
    enabled: settings.handsFree, needConfirm: pendingOps.length > 0,
    onPartial: (t) => core.draftUser(t),
    onSend: (text) => onSend(text, undefined, { source: 'handsfree', bubbleId: core.commitDraftUser() ?? undefined }),
    onS2sUserUtterance: (t) => core.s2sUserUtterance(t),
    onS2sAnswerDelta: (t) => core.s2sAnswerDelta(t),
    onS2sTurnEnd: (r) => core.s2sTurnEnd(r.reason),
    onS2sEscalated: (text) => onSend(text, undefined, { source: 's2s', bubbleId: core.takeS2sUserBubble() ?? undefined }),
    onNotice: setNotice, onCancelTurn: () => core.cancelCurrentTurn(),
  })
  useEffect(() => {
    // PTT 的草稿不能被空闲 HF 的 effect 擦掉。
    if (hf.fsm !== 'LISTENING' && ptt.state === 'idle') core.discardDraftUser()
  }, [hf.fsm, ptt.state, core])
  const win = useWindowDimensions()
  // 隐私栏的「当前：xx」取**服务端身份**（AR05 R14）。取不到才回落 token 尾 4 位——
  // 那从来不是用户是谁，只是一段凭证的尾巴。配置一变先清空：旧账号的摘要绝不能
  // 留在新会话上（同 ensureWired/disposeWired 的销毁边界）。
  const [serverUserId, setServerUserId] = useState('')
  useEffect(() => {
    setServerUserId('')
    if (!cfg.edgeUrl || !cfg.token) return
    let alive = true
    void fetchSessionInfo(cfg.edgeUrl, cfg.token).then((r) => {
      if (alive && r.kind === 'ok' && r.summary.userId) setServerUserId(r.summary.userId)
    })
    return () => { alive = false }
  }, [cfg.edgeUrl, cfg.token])
  const snapshot = usePresence({ core, hf, ptt: cfg.audioUrl ? ptt : null, user: serverUserId || cfg.token.slice(-4), sheetOverride, landscape: win.width > win.height, interactive: scope.canPresent() })
  const layout = useLayout(snapshot.driving)
  const reduceMotion = useReduceMotion()
  const motionEnv = { reduceMotion }
  useEffect(() => {
    const sc = speechController()
    const sync = () => {
      sc.setForeground(scope.canCapture())
      if (!scope.canCapture()) { cancelVisionCapture(); core.discardDraftUser(); core.s2sTurnEnd('cancelled') }
    }
    const off = scope.subscribe(sync)
    sync()
    // 新旧控制器的 passive cleanup 已结束后才能给新配置开闸。
    scope.update({ configured: true })
    return () => { off(); sc.setForeground(false) }
  }, [scope, core, cfg])
  useEffect(() => {
    const s2sBusy = settings.voicePipeline === 's2s' && ['LISTENING', 'THINKING', 'SPEAKING'].includes(hf.fsm)
    speechController().setProactiveCtx({ driving: snapshot.driving, s2sBusy })
  }, [snapshot.driving, hf.fsm, settings.voicePipeline])
  const prevFoldRef = useRef(layout.fold)
  useEffect(() => {
    const sw = screenSwitch(prevFoldRef.current, layout.fold)
    prevFoldRef.current = layout.fold
    if (!sw || ptt.state !== 'recording') return
    if (ptt.mode === 'hold') ptt.pressUp()
    else if (ptt.mode === 'tap') ptt.tap()
  }, [layout.fold, ptt])
  const hfOn = settings.handsFree && hf.availability.usable
  const startListening = useCallback(() => {
    if (!scope.canCapture()) return
    if (!hfOn) ptt.tap()
    else if (hf.fsm === 'LISTENING') hf.endUtterance()
    else hf.wake()
  }, [hfOn, hf, ptt, scope])
  const onOrbTap = useCallback(() => {
    if (!scope.canCapture()) return
    if (snapshot.agent === 'speaking') { core.cancelCurrentTurn(); startListening() }
    else if (snapshot.agent === 'thinking' || snapshot.agent === 'processing') setSheetOverride({ turnId: latestTurnId, mode: 'open' })
    else startListening()
  }, [snapshot.agent, core, startListening, latestTurnId, scope])
  const onStopPlayback = useCallback(() => stopPlayback({ handsFree: hf, speech: speechController() }), [hf])
  const stopMic = useCallback(() => { ptt.cancel(); hf.pause() }, [ptt, hf])
  useEffect(() => {
    if (snapshot.privacy.micActive) activityLog.push('mic', '麦克风已开启（设备采集）')
  }, [snapshot.privacy.micActive])
  // 展开状态属于这一组稳定 ID；整组到期后自然失效，不需要 effect 再清一次 state。
  const dockExpanded = snapshot.commitment.some((item) => dockOpenedIds.includes(`${item.kind}:${item.id}`))
  const setDockExpanded = useCallback((expanded: boolean) => {
    setDockOpenedIds(expanded ? snapshot.commitment.map((item) => `${item.kind}:${item.id}`) : [])
  }, [snapshot.commitment])
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (!scope.canPresent()) return false
      if (privacyOpen) { setPrivacyOpen(false); return true }
      if (snapshot.input === 'voice-sheet' && !sheetResident(snapshot.identity, snapshot.driving)) {
        setSheetOverride({ turnId: latestTurnId, mode: 'dismissed' }); return true
      }
      return false
    })
    return () => sub.remove()
  }, [privacyOpen, snapshot.input, snapshot.identity, snapshot.driving, latestTurnId, scope])
  const busy = messages.some((m) => m.pending || m.streaming || m.processActive)
  const playback = useSyncExternalStore(subscribeAudioPlayback, getAudioPlaybackSnapshot)
  const stoppable = canStopPlayback({ playing: playback.playing, live: playback.live, busy })
  return {
    wired, cfg, core, state, settings, p, scope, facts, snapshot, layout, motionEnv, reduceMotion,
    ptt, hf, notice, turn, latestTurnId, busy, stoppable,
    sheetOverride, setSheetOverride, privacyOpen, setPrivacyOpen, dockExpanded, setDockExpanded, draft, setDraft,
    onSend, onConfirm, onSlotReply, onIssueAction, onInterrupt, onOrbTap, onStopPlayback, stopMic,
  }
}

export type AssistantRuntime = ReturnType<typeof useAssistantRuntime>

function ReadyAssistant({ children, ...opts }: Connection & { scope: InteractionScope; children: ReactNode }) {
  const runtime = useAssistantRuntime(opts)
  const modalOpen = runtime.privacyOpen || runtime.dockExpanded
  useLayoutEffect(() => modalOpen ? opts.scope.blockPresentation() : undefined, [modalOpen, opts.scope])
  return <AssistantContext.Provider value={runtime}>
    <VisionCapture enabled={runtime.settings.visionEnabled} scope={runtime.scope} />
    {children}
    <PrivacyRail p={runtime.p} fontScale={runtime.settings.fontScale} snapshot={runtime.snapshot}
      visible={runtime.privacyOpen && runtime.scope.canCapture()} onClose={() => runtime.setPrivacyOpen(false)} onStopMic={runtime.stopMic} />
  </AssistantContext.Provider>
}
