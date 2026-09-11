// mobile/src/features/chat/PresenceCapsule.tsx
// 状态胶囊（方案 §4.3）：替代 v1 的四条窄条——一次只说一件「此刻」的事；「欠着」的归 Dock。
// 材质 G1-tint（叠在深空底上）；文字不用极光（虹彩纪律）。
// 2026-09-11 两档制：它是胶囊类 ⇒ 走 `ui/Pill`（外框 = 触控目标 48 / 56、视觉 36 / 44）。原来视觉 26 靠
// hitSlop 补热区——同一行里它比支持页的动作键 / 光球矮一截，正是用户看到的「胶囊高度不一致」。
import { View } from 'react-native'

import type { PresenceSnapshot } from '@/core/presence/presence'
import { Pill } from '@/ui/Pill'
import { TYPE } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'
import type { FontScalePref } from '@/core/settings/store'

export function PresenceCapsule({
  p,
  fontScale,
  snapshot,
  onPress,
  solid = false,
}: {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onPress?(): void
  /** 支持页浮动在场（AR04 第十五节）：压在地图瓦片 / 设置文字上 ⇒ 实色底（map.tsx「压在不可控内容上的浮层一律不透明」） */
  solid?: boolean
}) {
  const c = snapshot.capsule
  if (!c) return null
  const fg = c.tone === 'amber' ? p.amber : c.tone === 'red' ? p.red : c.tone === 'accent' ? p.accent : p.fg2
  return (
    <View style={{ alignItems: 'center' }}>
      <Pill
        p={p}
        testID="presence-capsule"
        tone="glass"
        solid={solid}
        elevated
        driving={snapshot.driving}
        fontScale={fontScale}
        onPress={onPress}
        // 接了 onPress 就是按钮：role 随之改（评审「别踩」①）
        accessibilityRole={onPress ? 'button' : 'text'}
        // 建议胶囊点按 = 开行车档（B4-10）：提示语要说对，action 由 derivePresence 给
        accessibilityHint={onPress ? (c.action === 'enable-driving' ? '开启行车档' : '打开语音层') : undefined}
        // §8.1 partial 节流：逐 token 的 partial 交给转写区按**稳定 segment** 播（B2 T4 那一层本来就是节流过的粒度），
        // 胶囊在识别中闭嘴——两个 live region 同时说会让 TalkBack 不断打断自己（B4-9）
        accessibilityLiveRegion={snapshot.capture === 'recognizing' ? 'none' : 'polite'}
        textColor={fg}
        fontSize={TYPE.micro + 1}
        paddingHorizontal={12}
        maxTextWidth={260}
        label={c.text}
      >
        {c.live ? (
          <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: p.accent, boxShadow: `0 0 8px ${p.accent}` }} />
        ) : null}
      </Pill>
    </View>
  )
}
