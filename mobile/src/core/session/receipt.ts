// mobile/src/core/session/receipt.ts
// 执行回执（方案 §5.3.2）：字段**全部来自已有数据，零后端改动**。
//  车控：已理解（紧邻上一条用户原话，与 Dock 标题同一份判据）/ 目标（vehState.vehicle_id，没有就「当前车辆」）
//       / 确认（本端台账 confirmLog[operationId]）/ 执行（action 帧 + final 时刻）。
//       「安全检查：车辆静止，允许执行」今天拿不到（VAL 只在拒绝时说话）——**留位不渲染**，随 Q16 来。
//  信息服务：_prov 展开（数据源 · 更新 · 定位 · 状态）；card_group 取主卡的 _prov（与 T8 同一份主卡判据）。
// 零 RN import。
import type { Msg, Provenance } from '@shared/types.ts'

import { splitCardGroup } from '../cards/cardGroup'
import { precedingUserUtterance } from './actionSummary'
import type { ConfirmEntry, TurnMeta } from './store'

export interface ActionReceipt {
  kind: 'action'
  understood: string
  target: string
  confirm: ConfirmEntry | null
  executed: { ok: boolean; at: number | null; types: string[] }
}

export interface InfoReceipt {
  kind: 'info'
  vendor: string
  fetchedAt: string
  located: boolean
  mode: Provenance['mode']
  note: string
}

export type Receipt = ActionReceipt | InfoReceipt

/** 卡的 _prov；card_group 取主卡的（同一份主卡判据）；没有就 null */
export function provOf(card: unknown): Provenance | null {
  const c = card as { type?: string; _prov?: Provenance; items?: unknown[] } | null | undefined
  if (!c) return null
  if (c.type === 'card_group') return provOf(splitCardGroup(c.items ?? []).main)
  return c._prov?.mode ? c._prov : null
}

export function buildReceipt(args: {
  messages: readonly Msg[]
  assistant: Msg
  turnMeta: Record<string, TurnMeta>
  confirmLog: Record<string, ConfirmEntry>
  vehicleId?: string
}): Receipt | null {
  const { messages, assistant, turnMeta, confirmLog } = args
  const meta = turnMeta[assistant.id]
  if (assistant.actions?.length) {
    const at = messages.findIndex((m) => m.id === assistant.id)
    const opId = meta?.operationId
    return {
      kind: 'action',
      understood: at >= 0 ? precedingUserUtterance(messages, at) : '',
      target: args.vehicleId || '当前车辆',
      confirm: opId && confirmLog[opId] ? confirmLog[opId] : null,
      executed: { ok: !assistant.error, at: meta?.finalAt ?? null, types: assistant.actions.map((x) => x.type) },
    }
  }
  const prov = provOf(assistant.uiCard)
  if (prov) {
    return {
      kind: 'info',
      vendor: prov.vendor ?? '',
      fetchedAt: prov.fetched_at ?? '',
      located: !!meta?.withLocation,
      mode: prov.mode,
      note: prov.note ?? '',
    }
  }
  return null
}
