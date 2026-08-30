// 在场收集器：把既有状态机的事实收齐 → derivePresence（纯函数）。
// 这里**不做任何判断**——所有「谁压过谁」都在 presence.ts 里且有测试；这里只负责订阅与计时。
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from 'zustand'

import { actionSummary } from '@/core/session/actionSummary'
import type { SessionCore } from '@/core/session/store'
import { currentTurn } from '@/core/session/turnView'
import { settingsStore } from '@/core/settings/store'
import { speechController } from '@/core/voice/speech'
import { isVisionCapturing, subscribeVisionCapturing } from '@/core/vision/frame'
import {
  ARMED_CAPSULE_MS,
  derivePresence,
  ERROR_SHOW_MS,
  NOTICE_SHOW_MS,
  RECONNECTING_GRACE_MS,
  type Degradation,
  type PresenceSnapshot,
  type VoiceFacts,
} from '@/core/presence/presence'

import type { HandsFreeUi } from './useHandsFree'
import type { PttHandle } from './usePtt'

/** 用户对语音层的显式操作，钉在某一轮上（换轮即失效） */
export interface SheetOverride {
  turnId: string
  mode: 'open' | 'dismissed'
}

export interface UsePresenceOpts {
  core: SessionCore
  hf: HandsFreeUi
  ptt: PttHandle | null
  /** token 对应的 user_id（隐私栏「当前：xx」）；ServerConfig 里没有就显示 token 尾 4 位 */
  user: string
  sheetOverride: SheetOverride | null
}

/** 只在这些秒级量变化时才需要重算：倒计时 / 3s 延迟 / 4s error / 8s 长任务 */
const TICK_MS = 1000

export function usePresence({ core, hf, ptt, user, sheetOverride }: UsePresenceOpts): PresenceSnapshot {
  const { messages, pendingOps, connStatus, pendingLocationText, queued, uncertainIds, turnMeta } = useStore(core.store)
  const { settings } = useStore(settingsStore)

  // 播报中 / 抓帧中：订阅式信号（Task 5）
  const [speaking, setSpeaking] = useState(() => speechController().speaking)
  const [visionCapturing, setVisionCapturing] = useState(isVisionCapturing)
  useEffect(() => speechController().subscribeSpeaking(setSpeaking), [])
  useEffect(() => subscribeVisionCapturing(setVisionCapturing), [])

  // connStatus 变化时刻（reconnecting 3s 延迟的基准）
  const connChangedAt = useRef(Date.now())
  const prevConn = useRef(connStatus)
  if (prevConn.current !== connStatus) {
    prevConn.current = connStatus
    connChangedAt.current = Date.now()
  }

  // hf.fsm 变化时刻（armed 胶囊 3s 的基准，评审 D2）：登记的是事实，判据在 presence.ts
  const hfFsmChangedAt = useRef(Date.now())
  const prevFsm = useRef(hf.fsm)
  if (prevFsm.current !== hf.fsm) {
    prevFsm.current = hf.fsm
    hfFsmChangedAt.current = Date.now()
  }

  // 挂载那一刻列表里就已经有的 error 气泡**不算刚发生**（第 2 批遗留③）：登记成 0 =
  // 永远过期，红胶囊只留给挂载之后新出现的那条。
  const seeded = useRef(false)
  if (!seeded.current) {
    seeded.current = true
    for (const m of messages) if (m.role === 'assistant' && m.error) seen.seed('err:' + m.id, 0)
  }

  // 最近一条错误（4s 短显）
  const lastError = useMemo(() => pickLastError(messages, (k) => seen.firstSeen(k)), [messages])

  // 语音层的三个事实（判据在 derivePresence）：这一轮谁发起、用户有没有下拉过、有没有字/卡
  const turn = currentTurn(messages)
  const latestTurnId = turn.assistant?.id ?? ''
  const voice: VoiceFacts = {
    turnSource: (latestTurnId && turnMeta[latestTurnId]?.source) || 'text',
    override: sheetOverride && sheetOverride.turnId === latestTurnId ? sheetOverride.mode : null,
    answer: !!turn.assistant?.text,
    card: !!turn.assistant?.uiCard,
  }

  // 在飞轮 + 过程区
  const active = [...messages].reverse().find((m) => m.role === 'assistant' && (m.pending || m.streaming || m.processActive))
  const processLabel = active?.process?.length ? active.process[active.process.length - 1].label : ''
  const processSince = active?.processActive ? seen.firstSeen('proc:' + active.id) : 0

  // 降级轴（§12.1）：B1 只接今天就有信号的四种
  const degradations: Degradation[] = []
  if (ptt?.errorKind === 'permission') degradations.push({ kind: 'permission_denied', what: 'mic', text: ptt.error })
  if (hf.bargeInDisabled) degradations.push({ kind: 'audio_echo_degraded', reason: hf.bargeInDisabled })
  if (hf.pipelineDegraded) degradations.push({ kind: 'service_degraded', text: hf.pipelineDegraded })
  if (uncertainIds.length) degradations.push({ kind: 'transport_unknown', messageIds: uncertainIds })

  // ── 秒级时钟 ──
  // `now` **每次渲染都取当前值**，tick 只负责「让它重渲一次」——把 now 存进 state 会在停表
  // 期间留下一个陈旧的读数，重新起表的**那一帧**倒计时就是错的。
  //
  // 而这块表**只在真的有人依赖 now 时才走**：无条件常开时真机实测（对话屏静置 10s、零交互、
  // 零消息）ChatBody 重渲 11 次、FlashList 可见气泡重渲 88 次——每秒把整列表重画一遍，
  // 而屏上什么都没变。下面四条是 `derivePresence` 里**全部**读 now 的分支，都不成立就停表。
  // 2s 提示：取消（§5.1.1 的隐私文案——「不会发给」而不是「未上传」）与回声（§5.2 规则 5）——取更晚的那个
  const cancelNotice = ptt?.cancelledAt ? { text: '已取消，这段话不会发给小舟', at: ptt.cancelledAt } : null
  const echoNotice = hf.echoAt ? { text: '像是我自己的声音，没算数', at: hf.echoAt } : null
  const notice = !cancelNotice ? echoNotice : !echoNotice ? cancelNotice : echoNotice.at > cancelNotice.at ? echoNotice : cancelNotice
  const now = Date.now()
  const [, bumpTick] = useState(0)
  const needsTick =
    pendingOps.length > 0 || // 确认卡倒计时（每秒要变）
    !!active?.processActive || // 长任务 8s 门槛
    (connStatus === 'connecting' && now - connChangedAt.current < RECONNECTING_GRACE_MS) || // 「正在重连…」3s 门槛
    (!!lastError && now - lastError.at < ERROR_SHOW_MS) || // error 胶囊 4s 短显
    (hf.fsm === 'ARMED' && now - hfFsmChangedAt.current < ARMED_CAPSULE_MS) || // armed 胶囊 3s 隐藏
    (!!notice && now - notice.at < NOTICE_SHOW_MS) // 取消 / 回声提示 2s 短显
  useEffect(() => {
    if (!needsTick) return
    const t = setInterval(() => bumpTick((n) => n + 1), TICK_MS)
    return () => clearInterval(t)
  }, [needsTick])

  // pendingOps 的摘要：**紧邻的上一条用户原话**（评审 D1）。带 operationId 的那条助手气泡
  // 对每个危险动作都是同一句通用话，不是摘要。判据在 actionSummary.ts，留痕行也从它取。
  const ops = pendingOps.map((op) => ({
    id: op.id,
    ts: op.ts,
    summary: actionSummary(messages, op.id) || '待确认的操作',
  }))

  return derivePresence({
    now,
    connStatus,
    connChangedAt: connChangedAt.current,
    hfEnabled: settings.handsFree,
    hfUsable: hf.availability.usable,
    hfFsm: hf.fsm,
    hfFsmChangedAt: hfFsmChangedAt.current,
    ptt: ptt?.state ?? 'idle',
    partial: ptt?.partial || hf.partial || '',
    turn: {
      pending: !!active?.pending,
      streaming: !!active?.streaming,
      processActive: !!active?.processActive,
      processLabel,
      processSince,
    },
    speaking,
    pendingOps: ops,
    pendingLocation: pendingLocationText !== null,
    voicePipeline: settings.voicePipeline,
    visionCapturing,
    queued,
    lastError,
    degradations,
    driving: !!active?.driving,
    identity: settings.deviceRole,
    user,
    voice,
    notice,
  })
}

// ── 时刻登记：`Msg` 是共享类型、不能加字段（计划 §0 第 5 条），所以「这条错误/过程是什么
//    时候出现的」记在这里。**它答的是「hook 第一次看见它」**——两者只在一种情形下不一样：
//    挂载那一刻列表里就已经有的那条。第 2 批遗留③的后果具体：几天前的一条 error 气泡会在
//    进屏时白闪一次 4s 红胶囊。B1 的判据是 `seed()`：挂载时就在的登记成一个永远过期的时刻，
//    只有挂载**之后**新出现的才算「刚发生」。──
export class SeenRegistry {
  private readonly map = new Map<string, number>()

  constructor(
    private readonly capacity = 200,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  /** 只在缺席时登记；已登记的不覆盖——第二次挂载的 seed 不许把新错误洗成旧的 */
  seed(key: string, at: number): void {
    if (!this.map.has(key)) this.map.set(key, at)
  }

  /** 第一次问到时登记 clock()，之后钟走了也返回同一个值 */
  firstSeen(key: string): number {
    const t = this.map.get(key)
    if (t !== undefined) return t
    const n = this.clock()
    this.map.set(key, n)
    if (this.map.size > this.capacity) this.map.delete(this.map.keys().next().value as string)
    return n
  }
}

/** 收集器共用的登记表（模块级：hook 重挂载时不重置，否则「刚发生」会被重挂载刷新一遍） */
const seen = new SeenRegistry()

export interface ErrorLike {
  id: string
  role: 'user' | 'assistant'
  text: string
  error?: boolean
}

/** 最后一条助手 error 气泡 → 胶囊要的 `{ text, at }`；`at` 由调用方给的登记表决定。 */
export function pickLastError(
  messages: readonly ErrorLike[],
  at: (key: string) => number,
): { text: string; at: number } | null {
  const m = [...messages].reverse().find((x) => x.role === 'assistant' && x.error)
  if (!m) return null
  return { text: m.text.startsWith('出错了') ? '出错了' : m.text.slice(0, 20), at: at('err:' + m.id) }
}
