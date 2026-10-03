// mobile/src/core/cards/cardActions.ts
// 卡内动作发出的那句话（v3 P4d）：全量卡与行车摘要读同一份。按钮语义见 hmi/src/types.ts 的 CardButton——
// 卡内动作只合成一句自然语言经普通 send 上行，不是业务写接口；危险动作仍由全局确认条二次确认。
// 句子原来写在各渲染器里（navCards / miscCards），行车摘要要给同一个主按钮，就得同一个函数出句子。
// 零 RN import。
import type { PoiListCard } from '@shared/types.ts'

/** 「导航去 X」：地点详情 / 周边详情的「导航去这里」、路线测算的「开始导航」、候选列表的默认点选 */
export function navToText(name: string): string {
  return `导航去${name}`
}

/** 候选列表点第 N 个：回填充电目的地 / 加为途经点 / 直接导航（PoiList 的 purpose 三态） */
export function poiPickText(card: Pick<PoiListCard, 'purpose' | 'destination'>, name: string): string {
  if (card.purpose === 'dest_choice') return name
  if (card.purpose === 'waypoint_choice') return `导航去${card.destination || ''}途经${name}`
  return navToText(name)
}

/** 行程卡点某天的某一站 */
export function tripStopText(dayIndex: number, name: string): string {
  return `导航去第${dayIndex}天的${name}`
}

/** 场景列表点一个场景 */
export function sceneOpenText(name: string): string {
  return `开启${name}`
}
