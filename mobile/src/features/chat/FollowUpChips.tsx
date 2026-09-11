// chips 行（语音层内）：横向、外框 = 触控目标、点按 = 普通 send。零判据——chips 由 followUps.ts 算。
// 高度走 `ui/Pill`（2026-09-11 两档制）：外框泊车 48 / 行车 56，视觉药丸 36 / 44。
import { ScrollView } from 'react-native'

import type { FollowUpChip } from '@/core/session/followUps'
import type { FontScalePref } from '@/core/settings/store'
import { Pill } from '@/ui/Pill'
import { TARGET } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export function FollowUpChips({
  p,
  fontScale,
  chips,
  target = TARGET.parked,
  onSend,
}: {
  p: Palette
  fontScale: FontScalePref
  chips: FollowUpChip[]
  /** 触控目标（dp）：行车档 TARGET.driving=56，其余 48（B4-11 / §6）。Pill 按它选行车 / 泊车两档 */
  target?: number
  onSend(text: string): void
}) {
  if (!chips.length) return null
  const driving = target >= TARGET.driving
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 2 }} style={{ alignSelf: 'stretch' }}>
      {chips.map((c) => (
        <Pill
          key={c.text}
          p={p}
          testID="followup-chip"
          tone="accent"
          driving={driving}
          fontScale={fontScale}
          label={c.label}
          onPress={() => onSend(c.text)}
        />
      ))}
    </ScrollView>
  )
}
