// mobile/src/core/voice/voiceCue.ts
// 唤醒应答 / 退场应答的人声片段（2026-10-11，设计 docs/design/2026-10-11-wake-acknowledgement.md §3.2）。
//
// 句子与 HMI 同一份（@shared/voiceCues.mjs）；用当前播报引擎 + 音色预合成成 PCM（tts.ts::collectTtsPcm，
// 流式优先、与正文同声同色），触发时随机播一条，把时长交给状态机——状态机在应答播完之前不开 ASR。
// 这里只管「有没有、播一条、多长」：什么时候播是状态机的判据（voiceLoop._decideWake），
// 播不播人声是控制器按设置决定（播报静音 / S2S / 手动唤醒都不播）。
import { EXIT_CUE_TEXTS, WAKE_CUE_TEXTS } from '@shared/voiceCues.mjs'

import { newPcmPlayer, peekSharedAudioContext, scheduleAudioIdle, sharedAudioContext } from './audioCtx'
import type { PcmPlayerLike } from './queuePlayer'
import { collectTtsPcm } from './tts'

export type VoiceCueKind = 'wake' | 'exit'

interface Clip {
  pcm: Int16Array
  sampleRate: number
  ms: number
}

/** 一句的合成上限：超时就跳过这一句（下一句照合） */
export const CUE_SYNTH_TIMEOUT_MS = 8000
/** 输出上下文没在跑（空闲挂起后 resume 是异步的）时，报给状态机的时长多算这么久——宁可晚开听一点，不把自己的尾音收进来 */
export const CUE_RESUME_ALLOWANCE_MS = 200
/** 在场层的唤醒提示音要让位多久：控制器登记后，在场层的那次「待命 → 收音」在这个时间内到达 */
export const WAKE_TONE_MUTE_MS = 1500

const SETS: readonly [VoiceCueKind, readonly string[]][] = [
  ['wake', WAKE_CUE_TEXTS],
  ['exit', EXIT_CUE_TEXTS],
]

let clips: Record<VoiceCueKind, Clip[]> = { wake: [], exit: [] }
let readyKey = ''
let gen = 0
let pending: Promise<boolean> | null = null
let pendingKey = ''
let player: PcmPlayerLike | null = null
const lastIndex: Record<VoiceCueKind, number> = { wake: -1, exit: -1 }
let wakeToneMutedUntil = 0

export function voiceCueKey(provider: string, voice: string): string {
  return `${provider}|${voice}`
}

/** 这一组音色的片段是否已就绪（唤醒类至少一条） */
export function voiceCuesReady(key: string): boolean {
  return readyKey === key && clips.wake.length > 0
}

/** 已就绪的片段条数（logcat 取证用，只有数字没有内容） */
export function voiceCueCounts(): Record<VoiceCueKind, number> {
  return { wake: clips.wake.length, exit: clips.exit.length }
}

/**
 * 按引擎 + 音色预合成一组。同一组已就绪即空转，正在合成就复用那一次；期间换了音色，旧的那次作废不落地。
 * 逐句串行（并发会顶到流式引擎的并发上限，同 HMI）；部分失败保留成功的；唤醒类一条都没成 ⇒ 返回 false、不替换旧的。
 */
export function prepareVoiceCues(audioUrl: string, provider: string, voice: string): Promise<boolean> {
  const key = voiceCueKey(provider, voice)
  if (voiceCuesReady(key)) return Promise.resolve(true)
  if (pending && pendingKey === key) return pending
  const my = ++gen
  pendingKey = key
  const run = (async () => {
    const next: Record<VoiceCueKind, Clip[]> = { wake: [], exit: [] }
    for (const [kind, texts] of SETS) {
      for (const text of texts) {
        try {
          const { pcm, sampleRate } = await collectTtsPcm({ audioUrl, provider, voice }, text, CUE_SYNTH_TIMEOUT_MS)
          next[kind].push({ pcm, sampleRate, ms: Math.round((pcm.length / sampleRate) * 1000) })
        } catch {
          /* 单句失败跳过 */
        }
        if (my !== gen) return false // 期间换了音色 / 清掉了：这一组作废
      }
    }
    if (my !== gen || !next.wake.length) return false
    clips = next
    readyKey = key
    lastIndex.wake = -1
    lastIndex.exit = -1
    return true
  })()
  pending = run
  void run.finally(() => {
    if (pending === run) {
      pending = null
      pendingKey = ''
    }
  })
  return run
}

/** 让输出上下文先跑起来：KWS 一命中就调，判断窗那 240ms 正好把空闲挂起后的 resume 等过去 */
export function warmCueOutput(): void {
  try {
    sharedAudioContext()
    scheduleAudioIdle()
  } catch {
    /* 音频上下文不可用：播的时候自然返回 0，状态机回落提示音 */
  }
}

/**
 * 随机播一条已就绪的片段（与上一条不同，只有一条时例外），返回报给状态机的时长 ms；
 * 不是这组音色 / 没就绪 / 播放失败 ⇒ 0（状态机回落提示音、立刻开听）。
 */
export function playVoiceCue(kind: VoiceCueKind, key: string): number {
  if (readyKey !== key) return 0
  const arr = clips[kind]
  if (!arr.length) return 0
  let i = Math.floor(Math.random() * arr.length)
  if (arr.length > 1 && i === lastIndex[kind]) i = (i + 1) % arr.length
  lastIndex[kind] = i
  const clip = arr[i]
  try {
    stopVoiceCue()
    const running = peekSharedAudioContext()?.state === 'running'
    const p = newPcmPlayer({ sampleRate: clip.sampleRate })
    p.push(clip.pcm)
    player = p
    scheduleAudioIdle()
    return clip.ms + (running ? 0 : CUE_RESUME_ALLOWANCE_MS)
  } catch {
    return 0
  }
}

/** 停掉正在播的应答（系统抢占 / 停播 / 拆机） */
export function stopVoiceCue(): void {
  const p = player
  player = null
  try {
    p?.stop()
  } catch {
    /* ignore */
  }
}

/** 清掉全部片段（播报改成静音 / 测试复位）；在合成的那一组作废 */
export function clearVoiceCues(): void {
  gen += 1
  clips = { wake: [], exit: [] }
  readyKey = ''
  pending = null
  pendingKey = ''
  lastIndex.wake = -1
  lastIndex.exit = -1
  stopVoiceCue()
}

/** 控制器决定这一次唤醒走人声应答：在场层那次「待命 → 收音」的两音提示让位（只让一次、只在短时间内有效） */
export function muteNextWakeTone(now: number = Date.now()): void {
  wakeToneMutedUntil = now + WAKE_TONE_MUTE_MS
}

/** 在场层播唤醒提示音前取一次：true = 这次让位给人声应答（取过即失效） */
export function takeWakeToneMuted(now: number = Date.now()): boolean {
  const muted = now < wakeToneMutedUntil
  wakeToneMutedUntil = 0
  return muted
}
