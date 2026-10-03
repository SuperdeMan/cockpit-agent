// mobile/src/core/cards/controlResult.ts
// 车控结果（v3 P4c，D17；Figma Card/ControlResult）：把一轮里的车控动作整理成「对象 · 目标值 · 状态」。
// 数据只读已有字段——`Msg.actions`（action 帧）与 `resultBundles[].results[].evidence`（CA2-10 执行证据），不改契约。
// 证据先过共享投影 `readResultBundles`（hmi/src/resultBundle.mjs）：词表外的值整条丢弃、`verified` 由它重算不信上游，
// 这里只按投影后的字段归档，不再写第二份证据判据。
//
// 状态（一项一个）：
//   - 这一轮还在流式 / 思考中、还没有结果行 ⇒ 执行中；
//   - 没有结果行（端侧快路径直接执行的大多数车控，这一路不做状态核验）⇒ 已执行；
//   - evidence.verified ⇒ 已核实；state=unsatisfied 或结果行失败 ⇒ 没生效；state=satisfied 且 observed=unchanged ⇒ 本来就是；
//   - 其余有证据但没核实的（含 pending_edge「已交给车端」）⇒ 未核实，pending_edge 带服务端原话。
//   - 气泡出错（请求失败 / 超时 / 断链）时动作帧已经到了 ⇒ 车端执行过、只是这一轮没正常收尾：判「未核实」，
//     不判「没生效」——方案原文写的是后者，那会对一个可能已经生效的动作说「没生效」（偏差记在执行记录）。
// 零 RN import。
import type { Action, Msg, ResultEntry } from '@shared/types.ts'
import { readResultBundles } from '@shared/resultBundle.mjs'

import { controlActionWord, controlFeatureName, controlObjectName } from './controlNames'

export type ControlStatus = 'executed' | 'verified' | 'unchanged' | 'unverified' | 'failed' | 'running'

export const STATUS_WORD: Readonly<Record<ControlStatus, string>> = {
  executed: '已执行',
  verified: '已核实',
  unchanged: '本来就是',
  unverified: '未核实',
  failed: '没生效',
  running: '执行中',
}

export interface ControlItem {
  kind: 'vehicle' | 'media'
  command: string
  /** 对象中文名（空调 / 车窗）；认不出 ⇒ '' */
  object: string
  /** 位置 + 对象 + 子功能（主驾座椅加热）；认不出对象 ⇒ '' */
  label: string
  /** 动作词（打开 / 关闭 / 设为 / 调高）；认不出 ⇒ '' */
  action: string
  /** 目标值（24°C / 3 档 / 50% / 运动模式）；没有 ⇒ '' */
  value: string
  /** value 是温度：「执行前已经是这个温度」/「目标温度」 */
  temperature: boolean
  status: ControlStatus
  /** 服务端原话（pending_edge 那句「该操作已交给车端，尚未核实执行结果。」）；其余 '' */
  note: string
}

type ResultRow = ResultEntry

const CJK = /[一-鿿]/
/** 开度类对象：端侧把「开一半」解析成 unit=percent 的 value（fast_intent 车窗 / 天窗 / 遮阳帘分支） */
const PERCENT_OBJECTS = new Set(['window', 'sunroof', 'sunshade'])

function str(v: unknown): string {
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return typeof v === 'string' ? v.trim() : ''
}

function isNumeric(v: string): boolean {
  return /^-?\d+(\.\d+)?$/.test(v)
}

function valueOf(command: string, payload: Record<string, unknown>): { value: string; temperature: boolean } {
  const temp = str(payload.temperature) || str(payload.temp)
  if (temp && isNumeric(temp)) return { value: `${temp}°C`, temperature: true }
  const raw = str(payload.value)
  const [head, mid] = command.split('.')
  if (raw && mid === 'wind_speed') return { value: `${raw} 档`, temperature: false }
  if (raw && PERCENT_OBJECTS.has(head) && isNumeric(raw)) return { value: `${raw}%`, temperature: false }
  const mode = str(payload.mode)
  if (mode && CJK.test(mode)) return { value: mode, temperature: false }
  return { value: raw, temperature: false }
}

/** 位置只取人话（「主驾」「副驾驶」）：端侧快路径带的是原话里的位置词；协议标识（front_left）不给人看 */
function positionsOf(payload: Record<string, unknown>): string {
  const raw = payload.positions
  const list = Array.isArray(raw) ? raw : typeof raw === 'string' ? [raw] : []
  return list.map(str).filter((x) => x && CJK.test(x)).join('、')
}

export function controlStatusOf(row: ResultRow | undefined, flags: { error: boolean; live: boolean }): ControlStatus {
  if (flags.error) return 'unverified'
  if (!row) return flags.live ? 'running' : 'executed'
  if (row.status === 'failed') return 'failed'
  const ev = row.evidence
  if (!ev) return 'executed'
  if (ev.verified) return 'verified'
  if (ev.state === 'unsatisfied') return 'failed'
  if (ev.state === 'satisfied' && ev.observed === 'unchanged') return 'unchanged'
  return 'unverified'
}

function isControlAction(a: Action | undefined): a is Action {
  return !!a && typeof a.type === 'string' && (a.type.startsWith('vehicle.control') || a.type.startsWith('media.control'))
}

/** 一轮里的车控 / 媒体控制动作 → 结果项（顺序同 action 帧）；没有就空数组 */
export function controlItems(msg: Msg): ControlItem[] {
  const actions = (msg.actions || []).filter(isControlAction)
  if (!actions.length) return []
  const rows = readResultBundles({ result_bundles: msg.resultBundles }).flatMap((b) => b.results)
  const used = new Set<ResultRow>()
  const flags = { error: !!msg.error, live: !!(msg.pending || msg.streaming) }
  return actions.map((a) => {
    const payload = (a.payload && typeof a.payload === 'object' ? a.payload : {}) as Record<string, unknown>
    const command = str(payload.command)
    const row = rows.find((r) => !used.has(r) && command && r.intent === command)
    if (row) used.add(row)
    const object = controlObjectName(command)
    const { value, temperature } = valueOf(command, payload)
    const status = controlStatusOf(row, flags)
    return {
      kind: a.type.startsWith('media.control') ? 'media' : 'vehicle',
      command,
      object,
      label: object ? `${positionsOf(payload)}${object}${controlFeatureName(command)}` : '',
      action: controlActionWord(command),
      value,
      temperature,
      status,
      note: status === 'unverified' && row?.pending_edge ? row.answer : '',
    }
  })
}

/** 一组结果的总状态（回执一行用）：最需要人留意的那个——没生效 > 未核实 > 执行中 > 已执行 > 本来就是 > 已核实 */
export function overallStatus(items: readonly ControlItem[]): ControlStatus | null {
  const order: ControlStatus[] = ['failed', 'unverified', 'running', 'executed', 'unchanged', 'verified']
  return order.find((s) => items.some((i) => i.status === s)) ?? null
}

/** 给人看的名字：认不出对象就说「车辆操作」，不显示机器名 */
export function itemName(item: ControlItem): string {
  return item.label || (item.kind === 'media' ? '媒体' : '车辆操作')
}
