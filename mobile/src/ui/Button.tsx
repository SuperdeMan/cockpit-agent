// mobile/src/ui/Button.tsx
// 按钮档（控件高度两档制里「视觉即外框」的那一档：Dock 确认 / 取消、卡尾动作、设置页按钮、引导页主键）。
// Android Visual v3（方向 B）：五种样式照 Figma Button 组件——
//   filled = accent 实色 + onAccent 字（主动作）；tonal = accentSoft + accent 字（次动作 / 重发）；
//   outlined = lineStrong 描边 + accent 字（卡内次要）；text = 只有字（取消）；destructive = redSoft + red 字（只在对话框里）。
// 高 = TARGET（泊车 48 / 行车 56，大字 ×1.1）；按下 = 字色 12% 叠层；禁用 38%。
// 胶囊档不用它（`ui/Pill.tsx`）；危险动作的确认只在全局 Dock，卡里不出 destructive。
import { Pressable, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'
import { Icon, iconRuntimeAvailable, type IconName } from './Icon'
import type { Palette } from './theme'
import { RADIUS, SPACE, TARGET, scale, textStyle } from './tokens'

export type ButtonVariant = 'filled' | 'tonal' | 'outlined' | 'text' | 'destructive'

export interface ButtonProps {
  p: Palette
  label: string
  variant?: ButtonVariant
  /** 前置图标（可选）；svg 原生缺席时只出文字 */
  icon?: IconName
  onPress?: () => void
  disabled?: boolean
  driving?: boolean
  fontScale?: FontScalePref
  testID?: string
  accessibilityLabel?: string
  accessibilityHint?: string
  /** 外框样式（flex、对齐、外边距）；高度与圆角不接受覆盖 */
  style?: StyleProp<ViewStyle>
}

/** 底 / 边 / 字色：一处决定 */
export function buttonColors(p: Palette, variant: ButtonVariant): { bg: string | undefined; border: string | undefined; fg: string } {
  switch (variant) {
    case 'filled':
      return { bg: p.accent, border: undefined, fg: p.onAccent }
    case 'tonal':
      return { bg: p.accentSoft, border: undefined, fg: p.accent }
    case 'outlined':
      return { bg: undefined, border: p.lineStrong, fg: p.accent }
    case 'text':
      return { bg: undefined, border: undefined, fg: p.accent }
    case 'destructive':
      return { bg: p.redSoft, border: undefined, fg: p.red }
  }
}

export function Button({
  p,
  label,
  variant = 'tonal',
  icon,
  onPress,
  disabled = false,
  driving = false,
  fontScale = 'normal',
  testID,
  accessibilityLabel,
  accessibilityHint,
  style,
}: ButtonProps) {
  const height = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const c = buttonColors(p, variant)
  const inert = disabled || !onPress
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityHint={accessibilityHint}
      accessibilityState={inert ? { disabled: true } : undefined}
      onPress={onPress}
      disabled={inert}
      style={[
        {
          height,
          paddingHorizontal: SPACE[4],
          borderRadius: RADIUS.full,
          backgroundColor: c.bg,
          borderWidth: c.border ? 1 : 0,
          borderColor: c.border,
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          gap: SPACE[1],
          overflow: 'hidden',
          opacity: disabled ? 0.38 : 1,
        },
        style,
      ]}
    >
      {({ pressed }) => (
        <>
          {pressed ? (
            <View
              testID={testID ? `${testID}-pressed` : undefined}
              pointerEvents="none"
              style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: c.fg, opacity: 0.12 }}
            />
          ) : null}
          {icon && iconRuntimeAvailable() ? <Icon name={icon} size={scale(18, 'text', fontScale)} color={c.fg} /> : null}
          <Text numberOfLines={1} style={[textStyle('labelL', fontScale), { color: c.fg }]}>
            {label}
          </Text>
        </>
      )}
    </Pressable>
  )
}
