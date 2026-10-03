// 追问 chips 行（语音层内；v3 P2b 起也在记录里每条回答的末尾）：横向、Accent 药丸、外框 = 触控目标、
// 点按 = 普通 send。零判据——chips 由 followUps.ts 算、由宿主决定给哪条回答。
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
          // B5-14（B4 Scanner 出账③「多个项目具有相同的说明」）：说明加前缀，否则 chip 与同文案的用户气泡
          // 读屏念成同一句、分不出哪个可点。只进 accessibilityLabel，视觉文案不变（原在 Composer chips 行，P2b 随 chips 挪来）
          accessibilityLabel={`追问：${c.label}`}
          driving={driving}
          fontScale={fontScale}
          label={c.label}
          onPress={() => onSend(c.text)}
        />
      ))}
    </ScrollView>
  )
}
