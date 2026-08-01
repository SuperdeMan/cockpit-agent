/** 主动消息的语音仲裁与补播队列（M-C）。
 *
 * 背景：S2S 交互进行中，主动 TTS 与模型音频是两个互不知情的播放器，同放=混音，
 * 且其生命周期回调会把 FSM 从 SPEAKING 误推 FOLLOWUP。验收当时的止血是**一刀切
 * 全都只出气泡**——信息不丢，但那条语音永远补不回来。
 *
 * 一刀切的根因不在 HMI 判断力，在**网关把 `priority` 吞掉了**：HMI 收到的信封里
 * 只有 speech/card/advisory，分不出哪条是「后备箱没关」哪条是「路况还行」。
 * 网关透传 priority 之后，仲裁就有依据了：
 *
 * - `critical`      安全播报不等人 → **抢话**（打断 S2S 在飞生成后立刻说）；
 * - `user_contract` 用户显式约定 → **排队**，S2S 空闲即补播（「到点提醒我」不能只是气泡）；
 * - 其余            → 只出气泡（维持既有行为）。
 *
 * 纯函数 + 纯数据队列，无 DOM、无定时器：判定与副作用分开，node 直接单测。
 */

export const SPEAK = 'speak'
export const INTERRUPT = 'interrupt'
export const DEFER = 'defer'
export const BUBBLE = 'bubble'

const CRITICAL = 'critical'
const USER_CONTRACT = 'user_contract'

/**
 * 这条主动消息此刻该怎么出声。
 *
 * @param {{priority?: string, hasText?: boolean, hasCard?: boolean}} msg
 * @param {{ttsEnabled?: boolean, autoplay?: boolean, s2sBusy?: boolean}} ctx
 * @returns {'speak'|'interrupt'|'defer'|'bubble'}
 */
export function decideSpeech(msg, ctx) {
  const { priority = '', hasText = false, hasCard = false } = msg || {}
  const { ttsEnabled = false, autoplay = false, s2sBusy = false } = ctx || {}
  // 朗读判据是 `text && card`（验收更正过的口径：不是「只有深调研会出声」，
  // 提醒到点/到地、场景建议、低电量建议都恒带卡）。
  if (!ttsEnabled || !autoplay || !hasText || !hasCard) return BUBBLE
  if (!s2sBusy) return SPEAK
  if (priority === CRITICAL) return INTERRUPT
  if (priority === USER_CONTRACT) return DEFER
  return BUBBLE
}

/** 待补播队列。**有界且按 delivery_id 去重**——补播不该把攒了半天的旧话一次倒出来。 */
export class PendingSpeech {
  constructor({ max = 3 } = {}) {
    this.max = max
    this.items = []
  }

  /** 入队；同一 delivery_id 只留一条。返回是否入队。 */
  push(entry) {
    const text = (entry && entry.text ? String(entry.text) : '').trim()
    if (!text) return false
    const id = entry.deliveryId || ''
    if (id && this.items.some((it) => it.deliveryId === id)) return false
    this.items.push({ text, deliveryId: id })
    // 溢出丢最旧的：攒太多说明用户很久没理它，最新的那条才是当下相关的。
    while (this.items.length > this.max) this.items.shift()
    return true
  }

  /** 取出全部待播（一次清空）。调用方负责实际播放。 */
  drain() {
    const out = this.items
    this.items = []
    return out
  }

  get size() {
    return this.items.length
  }
}

/** 从主动信封里取回执凭据（合并组带整组）。永远返回数组。 */
export function deliveryIdsOf(data) {
  if (!data) return []
  const many = data.delivery_ids
  if (Array.isArray(many)) return many.filter((x) => typeof x === 'string' && x)
  const one = data.delivery_id
  return typeof one === 'string' && one ? [one] : []
}
