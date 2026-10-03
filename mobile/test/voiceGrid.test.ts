// 音色格列数（v3 P7 / P5a 回归）：2026-10-03 OPPO 外屏实拍，MiniMax 的四字名在三列格里被截成「甜美…」「青涩…」。
// 用例取共享目录里各引擎的真实名字（不另抄名单），宽度取那台外屏量到的格子区宽 294dp、labelM 13sp。
import { TTS_PROVIDER_FALLBACK } from '@shared/types.ts'

import { labelUnits, voiceGridColumns } from '@/features/settings/voiceGrid'

const names = (id: string) => {
  const provider = TTS_PROVIDER_FALLBACK.find((p) => p.id === id)
  if (!provider) throw new Error(`共享目录里没有引擎 ${id}`)
  return provider.voices.map((v) => v.name)
}

test('四字名（MiniMax）在手机外屏退两列；两字名（MiMo）保持设计稿的三列', () => {
  expect(Math.max(...names('minimax').map(labelUnits))).toBe(4) // 前提：目录里真有四字名，否则这条守卫恒绿
  expect(voiceGridColumns(294, names('minimax'), 13)).toBe(2)
  expect(voiceGridColumns(294, names('mimo'), 13)).toBe(3)
})

test('还没量到宽度时沿用三列；再窄也至少一列', () => {
  expect(voiceGridColumns(0, names('minimax'), 13)).toBe(3)
  expect(voiceGridColumns(60, names('minimax'), 13)).toBe(1)
})
