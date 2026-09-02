// mobile/src/features/chat/PresenceCapsule.tsx
// 状态胶囊（方案 §4.3）：替代 v1 的四条窄条——一次只说一件「此刻」的事；「欠着」的归 Dock。
// 材质 G1-tint（叠在深空底上）；文字不用极光（虹彩纪律）。
import { Pressable, Text, View } from 'react-native'

import type { PresenceSnapshot } from '@/core/presence/presence'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
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
        disabled={!onPress}
        // 接了 onPress 就是按钮：role 与热区一起改（评审「别踩」①）——视觉仍 26dp，
        // hitSlop 补到 48dp（Material 触控目标是**可点区域**不是球体，方案 §8.1）
        accessibilityRole={onPress ? 'button' : 'text'}
        // 建议胶囊点按 = 开行车档（B4-10）：提示语要说对，action 由 derivePresence 给
        accessibilityHint={onPress ? (c.action === 'enable-driving' ? '开启行车档' : '打开语音层') : undefined}
        // 行车档目标 56（B4-11 / §6「目标 ≥56dp」）：视觉仍 26dp，靠 hitSlop 补到位。
        // ⚠ `maestro hierarchy` 量的是**视觉 bounds**，量不到 hitSlop——target_probe 上这一个
        //   FAIL 是读法的限制不是缺陷（T13 步骤 3 单列说明）
        hitSlop={onPress ? Math.ceil(((snapshot.driving ? TARGET.driving : TARGET.parked) - 26) / 2) : undefined}
        // §8.1 partial 节流：逐 token 的 partial 交给转写区按**稳定 segment** 播（B2 T4 那一层本来就是节流过的粒度），
        // 胶囊在识别中闭嘴——两个 live region 同时说会让 TalkBack 不断打断自己（B4-9）
        accessibilityLiveRegion={snapshot.capture === 'recognizing' ? 'none' : 'polite'}
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          minHeight: scale(26, 'target', fontScale),
          paddingHorizontal: 12,
          borderRadius: RADIUS.full,
          // §8「浅色主题下胶囊 / Dock 用不透明底」——Dock 已 G0，胶囊补上（B4-8）；深色仍 G1-tint
          backgroundColor: p.dark ? p.glassBg : '#FFFFFF',
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
