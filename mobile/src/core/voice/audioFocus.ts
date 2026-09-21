// 音频焦点与中断（实施计划 M2-4）。
//
// 两条处置刻意不同，理由都写在这，别按「对称」改成一样：
//  · **中断开始**（来电/闹钟/别的 App 抢焦点）→ 停播报。
//    **恢复时不自动续播**（计划原文）——被电话打断后自己接着念半句比不念更奇怪，
//    用户重新问一句的成本远低于「它突然又说话了」的惊吓。
//  · **设备拔出**（`OldDeviceUnavailable`：拔耳机/蓝牙断开）→ 停播报。
//    这是 Android 的 becoming-noisy 语义：拔了耳机还继续外放，等于把刚才那句私密内容
//    广播给一车人。**设备接入**（NewDeviceAvailable）不停——那不是隐私事件。
//
// M4 补一条**可观测性**（M3 遗留 R2 的真正障碍）：原来 `onEvent` 只是个形参，
// 全仓没有任何消费方 ⇒ 「来电/闹钟/抢焦点四场景」根本**看不见事件有没有到**，
// 只能靠「听起来停了吗」这种主观读数。而这条链的第一个疑点恰恰是
// 「react-native-audio-api 在这台设备上到底发不发这个事件」——那是原生绑定，
// 静默不发是完全可能的。⇒ 事件一律记进有界日志，spike 屏直接读。
// （「声明存在≠能用」，M-B/M-C/M-D 验收那批的老账。）
//
// 2026-09-19（GPT-6 评审 F02）：**停播动作不再由本文件自己决定怎么停**。此前两条处置都直接
// `speechController().stop()`，而 `stopPlayback.ts` 早已写明「必须先 `handsFree.stopSpeaking()` 再停
// `SpeechController`」——反过来 `onSpeechEnded → ttsEnd()` 会把免唤醒 FSM 从 SPEAKING 推进 FOLLOWUP
// （8s 免唤醒续问窗：来电挂断后麦却开着），且 S2S 自答走 HandsFreeController 手里的播放器、根本不经
// SpeechController。屏上的停止键早已走统一出口，系统事件却还在走单一路径。⇒ 本文件只认一个
// `systemStop` 出口：缺省仍是只停主链（Provider 没装配时没有免唤醒，行为与 M2-4 逐字相同）；
// AssistantProvider 装配后 `bindSystemStop` 换成 `stopPlayback({ handsFree, speech })` 那一份。
// 系统抢占按「用户停播」语义收尾：FSM → ARMED、不开续问窗、恢复焦点不续播。
//
// 2026-09-20（D-09 取证前核源码）：**Android 上库的 `routeChange` 是死监听**——react-native-audio-api 0.13.3 的
// Android 端只有 AudioFocusListener（焦点变化 ⇒ interruption / duck），`AudioEvent.ROUTE_CHANGE` 没有任何
// 调用点；「拔耳机 ⇒ 停播」在 Android 上从来没成立过。系统给的事实是 ACTION_AUDIO_BECOMING_NOISY 广播，
// 由本仓库 `modules/audioroute` 透传成 `onBecomingNoisy`，这里按 OldDeviceUnavailable 同一条处置停播
// （库的 routeChange 监听照留：iOS 走它）。取证先看 `audioRouteInstalled()`，再看事件到没到。
//
// 2026-09-20（D-09 真机核出的第二件事）：**焦点不能在启动时请求一次然后永久持有**。库的
// `observeAudioInterruptions(true)` 就是这么做的（Android 端 = 请求一次 AUDIOFOCUS_GAIN），真机读数两条：
//  ① 打开 App 就把用户正在放的音乐永久停掉（GAIN 对别的持有者是永久 LOSS）；
//  ② 第一次被别的媒体 App 永久抢走（onAudioFocusChange(-1)）后我们的条目被移出焦点栈，之后再来的来电 / 闹钟
//     **一个回调都到不了**（OPPO：视频播放器抢走之后计时器响铃，App 收到 0 次回调）——中断检测只活到第一次 LOSS。
// ⇒ 改成**出声才持焦点**：任一路播放通道活着（`audioPlaybackLive`：会话开着 / 播放器建好 / 在出声）就请求
// GAIN_TRANSIENT_MAY_DUCK（别人的音乐压低、不停），全部收尾后过 FOCUS_RELEASE_GRACE_MS 放掉（分段播报的
// 段间不抖动、音乐回到原音量）；每次起播都重新请求，永久 LOSS 之后下一次出声检测又活了。不出声的时候不持焦点：
// 那时来电 / 闹钟本来也没有播放可停。
//
// 2026-09-21（G-07，用户裁决「都做」）：**收音期也持焦点**。真机读数：LISTENING 期不持焦点 ⇒ 来电 / 闹钟零回调、麦照开、
// ASR 照传、铃声灌进麦把端点拖到铃停；FOLLOWUP 的 8s 免唤醒窗在来电时开着等于隐式采集。⇒ 持焦点的理由有三种，
// 任一成立就持：播放通道活着（playbackFacts）、上行采集中（captureFacts 的 asrUploading / s2sUploading：PTT 与
// 免唤醒 LISTENING 都算）、免唤醒热窗（useHandsFree 按 FSM 报的 LISTENING / FOLLOWUP：FOLLOWUP 的空窗里 ASR 还没开，
// 单看采集事实会漏掉它）。收音期持 GAIN_TRANSIENT_MAY_DUCK 的顺带收益：别人的音乐在我们听的时候压低，ASR 听得清。
// 中断到了走同一个 systemStop 出口——Provider 装配的那份会把回路的收音 / 续问窗一起放弃（voiceLoop.systemInterrupt）。
import AudioRouteNative from '../../../modules/audioroute'

import { getAudioCaptureSnapshot, subscribeAudioCapture } from './captureFacts'
import { audioPlaybackLive, subscribeAudioPlayback } from './playbackFacts'
import { speechController } from './speech'

/* eslint-disable @typescript-eslint/no-require-imports */

let installed = false
/** 出声才持焦点：请求的焦点类型（库的 AudioFocusType 串） */
export const FOCUS_TYPE = 'gainTransientMayDuck'
/** 全部播放收尾后再等这么久才放焦点：分段播报段间、批处理兜底切换都在这个窗口内，不让别人的音乐抖 */
export const FOCUS_RELEASE_GRACE_MS = 1000
let audioManager: { observeAudioInterruptions(param: string | boolean): void } | null = null
let focusHeld = false
let releaseTimer: ReturnType<typeof setTimeout> | null = null
let playbackUnsub: (() => void) | null = null
let captureUnsub: (() => void) | null = null
/** 持焦点的理由（G-07）：任一在场就持。'playback' / 'capture' 由两份事实自动喂，'handsfree-hot' 由 useHandsFree 按 FSM 报 */
export type FocusHoldReason = 'playback' | 'capture' | 'handsfree-hot'
const holdReasons = new Set<FocusHoldReason>()
/** becoming-noisy 原生接收装上了没有（Android；旧 APK / iOS 上是 false，拔耳机那一维走不到） */
let routeInstalled = false
let routeSub: { remove(): void } | null = null

export type SystemStopReason = 'interruption' | 'routeChange'

const defaultStop = (_reason: SystemStopReason): void => speechController().stop()
let systemStop: (reason: SystemStopReason) => void = defaultStop

/** 装配系统抢占时的停播出口（返回解除函数；解除时回到只停主链的缺省）。传 null 等于解除。 */
export function bindSystemStop(handler: ((reason: SystemStopReason) => void) | null): () => void {
  const bound = handler ?? defaultStop
  systemStop = bound
  return () => { if (systemStop === bound) systemStop = defaultStop }
}

/** 当前装配的是不是统一出口——取证时先看这一位：事件到了但只停了主链，是装配缺席不是判据错 */
export function systemStopBound(): boolean {
  return systemStop !== defaultStop
}

export interface AudioFocusEvent {
  /** focus = 我们自己请求 / 放掉焦点（纯观测：真机上对照 dumpsys audio 的焦点栈） */
  kind: 'interruption' | 'routeChange' | 'focus'
  detail: string
  stoppedPlayback: boolean
  /** 停播走的是哪条出口：unified = Provider 装配的统一语义；speech = 只停主链的缺省 */
  stoppedVia?: 'unified' | 'speech'
}

/** 系统抢占 → 停播。handler 抛错不能拦住事件记录（日志是取证的唯一读数） */
function stopForSystem(reason: SystemStopReason): 'unified' | 'speech' {
  const via = systemStopBound() ? 'unified' : 'speech'
  try {
    systemStop(reason)
  } catch {
    /* 停播出口自己的异常留给它的实现记录 */
  }
  return via
}

export interface LoggedAudioFocusEvent extends AudioFocusEvent {
  /** 墙钟毫秒——取证时要和 adb 侧的事件时刻对得上 */
  at: number
}

/** 有界事件日志（最近 30 条）。**只增不改行为**：生产路径一行未变，纯观测。 */
const LOG_CAP = 30
const log: LoggedAudioFocusEvent[] = []
const watchers = new Set<(e: LoggedAudioFocusEvent) => void>()

function record(e: AudioFocusEvent): void {
  const entry: LoggedAudioFocusEvent = { ...e, at: Date.now() }
  log.push(entry)
  if (log.length > LOG_CAP) log.shift()
  for (const w of watchers) {
    try {
      w(entry)
    } catch {
      /* 一个观察者抛异常不该影响别人，更不该影响停播 */
    }
  }
}

/** 读日志（最新在后）。spike 屏与验收取证用。 */
export function audioFocusLog(): readonly LoggedAudioFocusEvent[] {
  return log
}

/** 订阅新事件；返回退订函数 */
export function watchAudioFocus(fn: (e: LoggedAudioFocusEvent) => void): () => void {
  watchers.add(fn)
  return () => watchers.delete(fn)
}

/** 原生监听到底装上了没有。false = 这台设备/这个 APK 上四场景**一个都不会到**，
 *  取证时先看这一位，别把「事件没来」读成「事件来了但没处理」。 */
export function audioFocusInstalled(): boolean {
  return installed
}

/** 此刻是不是持着焦点（按本文件的账；系统那边的真值是 dumpsys audio 焦点栈里有没有我们的条目） */
export function audioFocusHeld(): boolean {
  return focusHeld
}

/** 此刻持焦点的理由（取证读数；空 = 不该持） */
export function audioFocusHoldReasons(): readonly FocusHoldReason[] {
  return [...holdReasons]
}

/** 出声 / 收音才持焦点（头注最后两段）：理由集合翻转就同步一次。请求同步、放掉带宽限。 */
function syncFocus(): void {
  const am = audioManager
  if (!am) return
  if (holdReasons.size > 0) {
    if (releaseTimer) {
      clearTimeout(releaseTimer)
      releaseTimer = null
    }
    if (focusHeld) return
    try {
      am.observeAudioInterruptions(FOCUS_TYPE)
      focusHeld = true
      record({ kind: 'focus', detail: 'request ' + FOCUS_TYPE + ' ' + [...holdReasons].join('+'), stoppedPlayback: false })
    } catch {
      focusHeld = false
    }
    return
  }
  if (!focusHeld || releaseTimer) return
  releaseTimer = setTimeout(() => {
    releaseTimer = null
    if (holdReasons.size > 0) return
    try {
      am.observeAudioInterruptions(false)
    } catch {
      /* 放不掉就放不掉：下一次起播会重新请求 */
    }
    focusHeld = false
    record({ kind: 'focus', detail: 'abandon', stoppedPlayback: false })
  }, FOCUS_RELEASE_GRACE_MS)
}

/** 登记 / 撤销一条持焦点的理由。'playback' / 'capture' 由事实订阅自动维护；'handsfree-hot' 由 useHandsFree 报。 */
export function setFocusHold(reason: FocusHoldReason, active: boolean): void {
  const had = holdReasons.has(reason)
  if (had === active) return
  if (active) holdReasons.add(reason)
  else holdReasons.delete(reason)
  syncFocus()
}

function syncFromFacts(): void {
  const c = getAudioCaptureSnapshot()
  // 两条一起改再同步一次：先算再登记，避免中间态各请求一次
  const wantPlayback = audioPlaybackLive()
  const wantCapture = c.asrUploading || c.s2sUploading
  const changed = (wantPlayback !== holdReasons.has('playback')) || (wantCapture !== holdReasons.has('capture'))
  if (!changed) return
  if (wantPlayback) holdReasons.add('playback')
  else holdReasons.delete('playback')
  if (wantCapture) holdReasons.add('capture')
  else holdReasons.delete('capture')
  syncFocus()
}

/** becoming-noisy（拔耳机 / 蓝牙断开）的原生接收装上了没有。false ⇒ 「耳机断开」那一维在这个 APK 上不会到。 */
export function audioRouteInstalled(): boolean {
  return routeInstalled
}

/** 拔耳机 / 蓝牙音频断开（Android ACTION_AUDIO_BECOMING_NOISY）：与库的 OldDeviceUnavailable 同一条处置 */
function onBecomingNoisy(e: { at?: number; count?: number } | undefined, onEvent?: (e: AudioFocusEvent) => void): void {
  const via = stopForSystem('routeChange')
  const ev: AudioFocusEvent = {
    kind: 'routeChange',
    detail: 'OldDeviceUnavailable becomingNoisy#' + String(e?.count ?? '?'),
    stoppedPlayback: true,
    stoppedVia: via,
  }
  record(ev)
  onEvent?.(ev)
}

function installRouteReceiver(onEvent?: (e: AudioFocusEvent) => void): void {
  if (routeInstalled || !AudioRouteNative) return
  try {
    routeSub = AudioRouteNative.addListener('onBecomingNoisy', (e) => onBecomingNoisy(e, onEvent))
    routeInstalled = true
  } catch {
    routeInstalled = false
  }
}

/** App 启动时装一次（幂等）。onEvent 供调试屏观测，生产不传。 */
export function installAudioFocusHandlers(onEvent?: (e: AudioFocusEvent) => void): void {
  // 两个来源各自幂等：库的焦点监听装不上（jest / 旧 dev-client）不该连带丢掉 becoming-noisy 那一路
  installRouteReceiver(onEvent)
  if (installed) return
  try {
    const { AudioManager } = require('react-native-audio-api')
    // 不在这里请求焦点（头注最后两段）：焦点跟播放 / 采集事实与免唤醒热窗走，见 syncFocus
    AudioManager.addSystemEventListener('interruption', (e: any) => {
      const began = e?.type === 'began'
      const via = began ? stopForSystem('interruption') : undefined
      const ev: AudioFocusEvent = {
        kind: 'interruption',
        detail: String(e?.type ?? '?') + ' shouldResume=' + String(e?.shouldResume ?? '?'),
        stoppedPlayback: began,
        ...(via ? { stoppedVia: via } : {}),
      }
      record(ev)
      onEvent?.(ev)
    })
    AudioManager.addSystemEventListener('routeChange', (e: any) => {
      const lost = e?.reason === 'OldDeviceUnavailable'
      const via = lost ? stopForSystem('routeChange') : undefined
      const ev: AudioFocusEvent = {
        kind: 'routeChange',
        detail: String(e?.reason ?? '?'),
        stoppedPlayback: lost,
        ...(via ? { stoppedVia: via } : {}),
      }
      record(ev)
      onEvent?.(ev)
    })
    installed = true
    audioManager = AudioManager
    playbackUnsub = subscribeAudioPlayback(syncFromFacts)
    captureUnsub = subscribeAudioCapture(syncFromFacts)
    syncFromFacts()
  } catch {
    // 原生模块不在（jest / 未装新 dev-client）：不装监听也不该拦住 App 启动
    installed = false
  }
}

/** 测试用：允许重新装载 */
export function resetAudioFocusForTest(): void {
  installed = false
  routeSub?.remove()
  routeSub = null
  routeInstalled = false
  playbackUnsub?.()
  playbackUnsub = null
  captureUnsub?.()
  captureUnsub = null
  if (releaseTimer) clearTimeout(releaseTimer)
  releaseTimer = null
  audioManager = null
  focusHeld = false
  holdReasons.clear()
  log.length = 0
  watchers.clear()
  systemStop = defaultStop
}
