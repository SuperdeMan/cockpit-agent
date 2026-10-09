// mobile/test/stageScene.test.ts
// 舞台场景（方案 §7.2）：映射表与 hmi 逐字对账（从源码读，hmi 不碰）。HMI Visual v2（I5）把判定从
// ContextualStage.tsx 挪进 stagePresentation.mjs::stageKind，地图族多了 place_list / place_detail。
import * as fs from 'fs'
import * as path from 'path'

import { STAGE_AGENDA_TYPES, STAGE_MAP_TYPES, stageScene } from '@/core/stage/stageScene'
import type { Msg } from '@shared/types.ts'

 
const msg = (uiCard?: any, role: 'user' | 'assistant' = 'assistant'): Msg =>
  ({ id: Math.random().toString(36).slice(2), role, text: '', uiCard }) as Msg

test('对账：STAGE_MAP_TYPES / STAGE_AGENDA_TYPES 与 hmi stagePresentation.mjs::stageKind 逐字一致', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '..', '..', 'hmi', 'src', 'stagePresentation.mjs'), 'utf8')
  const family = (kind: string) => {
    const m = new RegExp(`if \\(\\[([^\\]]+)\\]\\.includes\\(card\\.type\\)\\) return '${kind}'`).exec(src)
    if (!m) throw new Error(`hmi stagePresentation.mjs 里找不到 ${kind} 族的判定——结构变了，先看它再改这条守卫`)
    return m[1].split(',').map((s) => s.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean)
  }
  const map = family('map')
  expect(map.length).toBeGreaterThan(2) // 结构性自检：解析成个位数说明正则失配
  expect([...STAGE_MAP_TYPES]).toEqual(map)
  expect([...STAGE_AGENDA_TYPES]).toEqual(family('agenda'))
})

test('无卡 → idle（用户气泡带卡也不算）', () => {
  expect(stageScene([msg(), msg({ type: 'weather' }, 'user')])).toEqual({ kind: 'idle' })
})

test('最近一张卡决定场景：天气 / 地图族 / 日程族 / 其余焦点', () => {
  expect(stageScene([msg({ type: 'weather' })]).kind).toBe('weather')
  expect(stageScene([msg({ type: 'weather' }), msg({ type: 'route_plan' })]).kind).toBe('map')
  expect(stageScene([msg({ type: 'reminder_list' })]).kind).toBe('agenda')
  expect(stageScene([msg({ type: 'stock_quote' })]).kind).toBe('focus')
})

test('card_group：主卡（display_priority 升序首张，判据在 cardGroup.ts）决定场景', () => {
  const s = stageScene([
    msg({
      type: 'card_group',
      items: [
        { type: 'poi_list', display_priority: 2 },
        { type: 'weather', display_priority: 0 },
      ],
    }),
  ])
  expect(s.kind).toBe('weather')
})
