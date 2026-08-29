// 在场收集器：把既有状态机的事实收齐 → derivePresence（纯函数）。
// 这里**不做任何判断**——所有「谁压过谁」都在 presence.ts 里且有测试；这里只负责订阅与计时。
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from 'zustand'

import type { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'
import { speechController } from '@/core/voice/speech'
import { isVisionCapturing, subscribeVisionCapturing } from '@/core/vision/frame'
import { derivePresence, type Degradation, type PresenceSnapshot } from '@/core/presence/presence'

import type { HandsFreeUi } from './useHandsFree'
import type { PttHandle } from './usePtt'

export interface UsePresenceOpts {
  core: SessionCore
  hf: HandsFreeUi
  ptt: PttHandle | null
  /** token 对应的 user_id（隐私栏「当前：xx」）；ServerConfig 里没有就显示 token 尾 4 位 */
  user: string
}

/** 只在这些秒级量变化时才需要重算：倒计时 / 3s 延迟 / 4s error / 8s 长任务 */
const TICK_MS = 1000

export function usePresence({ core, hf, ptt, user }: UsePresenceOpts): PresenceSnapshot {
  const { messages, pendingOps, connStatus, pendingLocationText, queued, uncertainIds } = useStore(core.store)
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

  // 最近一条错误（4s 短显）：取最后一条 error 气泡的出现时刻
  const lastError = useMemo(() => {
    const m = [...messages].reverse().find((x) => x.role === 'assistant' && x.error)
    return m ? { text: m.text.startsWith('出错了') ? '出错了' : m.text.slice(0, 20), at: errorSeenAt(m.id) } : null
  }, [messages])

  // 秒级时钟：只驱动依赖 now 的分支
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(t)
  }, [])

  // 在飞轮 + 过程区
  const active = [...messages].reverse().find((m) => m.role === 'assistant' && (m.pending || m.streaming || m.processActive))
  const processLabel = active?.process?.length ? active.process[active.process.length - 1].label : ''
  const processSince = active?.processActive ? processSeenAt(active.id) : 0

  // 降级轴（§12.1）：B1 只接今天就有信号的四种
  const degradations: Degradation[] = []
  if (ptt?.errorKind === 'permission') degradations.push({ kind: 'permission_denied', what: 'mic', text: ptt.error })
  if (hf.bargeInDisabled) degradations.push({ kind: 'audio_echo_degraded', reason: hf.bargeInDisabled })
  if (hf.pipelineDegraded) degradations.push({ kind: 'service_degraded', text: hf.pipelineDegraded })
  if (uncertainIds.length) degradations.push({ kind: 'transport_unknown', messageIds: uncertainIds })

  // pendingOps 的摘要：带该 operationId 的助手气泡原话
  const ops = pendingOps.map((op) => ({
    id: op.id,
    ts: op.ts,
    summary: (messages.find((m) => m.operationId === op.id)?.text || '待确认的操作').replace(/\s+/g, ' ').slice(0, 24),
  }))

  return derivePresence({
    now,
    connStatus,
    connChangedAt: connChangedAt.current,
    hfEnabled: settings.handsFree,
    hfUsable: hf.availability.usable,
    hfFsm: hf.fsm,
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
  })
}

// ── 时刻登记：Msg 类型是共享的、不能加字段，所以「这条错误/过程是什么时候出现的」在这里记 ──
const seenAt = new Map<string, number>()
function firstSeen(key: string): number {
  const t = seenAt.get(key)
  if (t) return t
  const n = Date.now()
  seenAt.set(key, n)
  if (seenAt.size > 200) seenAt.delete(seenAt.keys().next().value as string)
  return n
}
function errorSeenAt(id: string): number {
  return firstSeen('err:' + id)
}
function processSeenAt(id: string): number {
  return firstSeen('proc:' + id)
}
