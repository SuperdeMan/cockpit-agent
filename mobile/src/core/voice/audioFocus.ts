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
import { speechController } from './speech'

/* eslint-disable @typescript-eslint/no-require-imports */

let installed = false

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
  kind: 'interruption' | 'routeChange'
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

/** App 启动时装一次（幂等）。onEvent 供调试屏观测，生产不传。 */
export function installAudioFocusHandlers(onEvent?: (e: AudioFocusEvent) => void): void {
  if (installed) return
  try {
    const { AudioManager } = require('react-native-audio-api')
    AudioManager.observeAudioInterruptions(true)
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
  } catch {
    // 原生模块不在（jest / 未装新 dev-client）：不装监听也不该拦住 App 启动
    installed = false
  }
}

/** 测试用：允许重新装载 */
export function resetAudioFocusForTest(): void {
  installed = false
  log.length = 0
  watchers.clear()
  systemStop = defaultStop
}
