// 音色 → 图标（v3 P5a，Figma S-2 音色格子）与 HMI 对账：**跨进程消费用测试对账声明源**。
// 真源写在 hmi/src/components/SettingsPanel.tsx（组件文件，不是共享模块），手机端存一份副本；
// 这里读那段 `VOICE_ICON` 字面量与回落行，逐项比——先证读到了，再比两个方向。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { VOICE_ICON, voiceIcon } from '@/core/settings/voiceIcon'

const src = readFileSync(resolve(__dirname, '../../hmi/src/components/SettingsPanel.tsx'), 'utf8')

function hmiVoiceIcons(): Record<string, string> {
  const block = /const VOICE_ICON: Record<string, IconName> = \{([^}]*)\}/.exec(src)
  if (!block) throw new Error('SettingsPanel.tsx 里找不到 VOICE_ICON 字面量——结构变了，先看它再改这条对账')
  const out: Record<string, string> = {}
  for (const m of block[1].matchAll(/([\p{L}\w]+):\s*'([a-z-]+)'/gu)) out[m[1]] = m[2]
  return out
}

test('音色图标表与 HMI 逐项一致（先证读到了 6 个）', () => {
  const hmi = hmiVoiceIcons()
  expect(Object.keys(hmi).length).toBeGreaterThanOrEqual(6)
  expect({ ...VOICE_ICON }).toEqual(hmi)
})

test('回落规则与 HMI 一致：认不出按性别给通用图标', () => {
  expect(src).toContain("(v.gender === 'female' ? 'voice-jasmine' : v.gender === 'male' ? 'voice-birch' : 'voice-soda')")
  expect(voiceIcon({ voice_id: 'x', name: 'y', gender: 'female' })).toBe('voice-jasmine')
  expect(voiceIcon({ voice_id: 'x', name: 'y', gender: 'male' })).toBe('voice-birch')
  expect(voiceIcon({ voice_id: 'x', name: 'y' })).toBe('voice-soda')
  expect(voiceIcon({ voice_id: '冰糖', name: '冰糖', gender: 'female' })).toBe('voice-ice')
  expect(voiceIcon({ voice_id: 'tts-07', name: 'Mia', gender: 'female' })).toBe('voice-mia')
})
