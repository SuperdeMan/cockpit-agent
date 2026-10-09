import type { Msg, ResultEntry } from './types'
export type ControlStatus = 'executed' | 'verified' | 'unchanged' | 'unverified' | 'failed' | 'running'
export interface ControlItem {
  kind: 'vehicle' | 'media'
  command: string
  object: string
  label: string
  action: string
  value: string
  temperature: boolean
  status: ControlStatus
  note: string
  sourceKind?: string
}
export const STATUS_WORD: Readonly<Record<ControlStatus, string>>
export function controlStatusOf(row: ResultEntry | undefined, flags: { error: boolean; live: boolean }): ControlStatus
export function controlItems(message: Msg): ControlItem[]
export function overallStatus(items: readonly ControlItem[]): ControlStatus | null
export function itemName(item: ControlItem): string
