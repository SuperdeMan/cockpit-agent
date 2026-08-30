// chips 行（语音层内）：横向、48dp 触控高度、点按 = 普通 send。零判据——chips 由 followUps.ts 算。
import { Pressable, ScrollView, Text } from 'react-native'

import type { FollowUpChip } from '@/core/session/followUps'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export function FollowUpChips({ p, fontScale, chips, onSend }: { p: Palette; fontScale: FontScalePref; chips: FollowUpChip[]; onSend(text: string): void }) {
  if (!chips.length) return null
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 2 }} style={{ alignSelf: 'stretch' }}>
      {chips.map((c) => (
        <Pressable
          key={c.text}
          testID="followup-chip"
          accessibilityRole="button"
          onPress={() => onSend(c.text)}
          style={{ minHeight: scale(TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 14, borderRadius: RADIUS.full, backgroundColor: p.accentSoft, borderWidth: 1, borderColor: p.accent }}
        >
          <Text style={{ color: p.accent, fontSize: scale(TYPE.caption + 1, 'text', fontScale) }}>{c.label}</Text>
        </Pressable>
      ))}
    </ScrollView>
  )
}
