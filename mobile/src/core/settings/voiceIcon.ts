// mobile/src/core/settings/voiceIcon.ts
// 音色 → 图标（v3 P5a，Figma S-2 音色格子）。**副本**：真源是 hmi/src/components/SettingsPanel.tsx 的
// `VOICE_ICON` / `voiceIcon`（写在组件文件里，不是共享模块）；test/voiceIcon.test.ts 读那段字面量逐项对账，
// 两边漂了直接红。按 voice_id 认，其次按名字，都认不出按性别给一个通用图标。零 RN import（图标名是类型导入）。
import type { IconName } from '../../ui/Icon'

export const VOICE_ICON: Readonly<Record<string, IconName>> = {
  冰糖: 'voice-ice',
  茉莉: 'voice-jasmine',
  苏打: 'voice-soda',
  白桦: 'voice-birch',
  Mia: 'voice-mia',
  Chloe: 'voice-chloe',
}

export function voiceIcon(v: { voice_id: string; name: string; gender?: string }): IconName {
  return VOICE_ICON[v.voice_id] ?? VOICE_ICON[v.name] ?? (v.gender === 'female' ? 'voice-jasmine' : v.gender === 'male' ? 'voice-birch' : 'voice-soda')
}
