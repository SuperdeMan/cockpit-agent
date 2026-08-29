// mobile/src/features/chat/PresenceCapsule.tsx
// 状态胶囊（方案 §4.3）：替代 v1 的四条窄条——一次只说一件「此刻」的事；「欠着」的归 Dock。
// 材质 G1-tint（叠在深空底上）；文字不用极光（虹彩纪律）。
import { Pressable, Text, View } from 'react-native'

import type { PresenceSnapshot } from '@/core/presence/presence'
import { RADIUS, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'
import type { FontScalePref } from '@/core/settings/store'

export function PresenceCapsule({
  p,
  fontScale,
  snapshot,
  onPress,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onPress?(): void
}) {
  const c = snapshot.capsule
  if (!c) return null
  const fg = c.tone === 'amber' ? p.amber : c.tone === 'red' ? p.red : c.tone === 'accent' ? p.accent : p.fg2
  return (
    <View style={{ alignItems: 'center', paddingVertical: 4 }}>
      <Pressable
        testID="presence-capsule"
        onPress={onPress}
        accessibilityRole="text"
        accessibilityLiveRegion="polite"
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          minHeight: scale(26, 'target', fontScale),
          paddingHorizontal: 12,
          borderRadius: RADIUS.full,
          backgroundColor: p.glassBg,
          borderWidth: 1,
          borderColor: p.glassBdRight,
          borderTopColor: p.glassBdTop,
          boxShadow: p.glassShadow,
        }}
      >
        {c.live ? (
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: p.accent, boxShadow: `0 0 8px ${p.accent}` }} />
        ) : null}
        <Text numberOfLines={1} style={{ color: fg, fontSize: scale(TYPE.micro + 1, 'text', fontScale), maxWidth: 260 }}>
          {c.text}
        </Text>
      </Pressable>
    </View>
  )
}
