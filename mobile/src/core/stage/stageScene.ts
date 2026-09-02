// mobile/src/core/stage/stageScene.ts
// 舞台场景选择（B4-5 / 方案 §7.2「舞台=卡的大视图」）：最近一张助手卡决定右舞台放什么——
// 日程族→日程、天气→天气、地图族→地图（卡自带「地图」入口）、其余→焦点卡、无卡→待机（车况三格）。
// 映射表与 hmi/src/components/ContextualStage.tsx::deriveScene 同一张；它住在 .tsx 组件里、不是共享模块
// （hmi/ 不碰），所以这里是**视图选择**的第二份实现——test/stageScene.test.ts 从 hmi 源码把 MAP_TYPES
// 字面读出来逐字比对，漂了当场红。与 HMI 的一处刻意差异：最近一张卡永远进舞台（非场景型 → focus），
// HMI 会跳过非场景卡去找更早的；M3-2 右舞台「焦点卡」段本来就是这个语义。纯函数、零 RN import。
import type { Msg, UiCard } from '@shared/types.ts'

import { splitCardGroup } from '../cards/cardGroup'

export const STAGE_MAP_TYPES = ['poi_list', 'poi_detail', 'route_plan', 'charging_route', 'trip_itinerary'] as const
export const STAGE_AGENDA_TYPES = ['reminder_list', 'reminder_card'] as const

export type StageScene = { kind: 'idle' } | { kind: 'weather' | 'map' | 'agenda' | 'focus'; card: UiCard }

/* eslint-disable @typescript-eslint/no-explicit-any */
function mainCard(card: any): UiCard | null {
  if (!card || typeof card !== 'object') return null
  if (card.type === 'card_group') return (splitCardGroup(card.items || []).main as UiCard | null) ?? null
  return card as UiCard
}

export function stageScene(messages: readonly Msg[]): StageScene {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.role !== 'assistant') continue
    const card = mainCard(m.uiCard)
    if (!card) continue
    const t = card.type as string
    if ((STAGE_AGENDA_TYPES as readonly string[]).includes(t)) return { kind: 'agenda', card }
    if (t === 'weather') return { kind: 'weather', card }
    if ((STAGE_MAP_TYPES as readonly string[]).includes(t)) return { kind: 'map', card }
    return { kind: 'focus', card }
  }
  return { kind: 'idle' }
}
