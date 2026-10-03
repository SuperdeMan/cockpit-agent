// mobile/src/core/session/receipt.ts
// 执行回执（方案 §5.3.2）：字段**全部来自已有数据，零后端改动**。
//  车控：已理解（紧邻上一条用户原话，与 Dock 标题同一份判据）/ 目标（vehState.vehicle_id，没有就「当前车辆」）
//       / 确认（本端台账 confirmLog[operationId]）/ 执行（总状态 + final 时刻 + 中文对象名）。
//       v3 P4c：「执行」行与车控结果卡读同一份逐项结果（core/cards/controlResult.ts）——不再显示 `vehicle.control`，
//       也不再把「气泡出错」说成「执行失败」：动作帧到了说明车端执行过，没收尾只能说「未核实」。
//       「安全检查：车辆静止，允许执行」今天拿不到（VAL 只在拒绝时说话）——**留位不渲染**，随 Q16 来。
//  信息服务：_prov 展开（数据源 · 更新 · 定位 · 状态）；card_group 取主卡的 _prov（与 T8 同一份主卡判据）。
// 零 RN import。
import type { Msg, Provenance } from '@shared/types.ts'

import { splitCardGroup } from '../cards/cardGroup'
import { controlItems, itemName, overallStatus, type ControlItem, type ControlStatus } from '../cards/controlResult'
import { precedingUserUtterance } from './actionSummary'
import type { ConfirmEntry, TurnMeta } from './store'

export interface ActionReceipt {
  kind: 'action'
  understood: string
  target: string
  confirm: ConfirmEntry | null
  /** 总状态（没生效 > 未核实 > 执行中 > 已执行 > 本来就是 > 已核实）+ final 时刻 + 中文对象名（认不出的不列） */
  executed: { status: ControlStatus; at: number | null; names: string[] }
  /** 逐项结果：车控结果卡读这一份，与「执行」行同源 */
  items: ControlItem[]
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
    const items = controlItems(assistant)
    // 非控制类动作（商户下单等）没有逐项结果：出错同样只能说「未核实」
    const status = overallStatus(items) ?? (assistant.error ? 'unverified' : 'executed')
    return {
      kind: 'action',
      understood: at >= 0 ? precedingUserUtterance(messages, at) : '',
      target: args.vehicleId || '当前车辆',
      confirm: opId && confirmLog[opId] ? confirmLog[opId] : null,
      executed: { status, at: meta?.finalAt ?? null, names: [...new Set(items.filter((i) => i.label).map(itemName))] },
      items,
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
