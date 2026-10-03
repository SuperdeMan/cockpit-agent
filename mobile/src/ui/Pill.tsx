// mobile/src/ui/Pill.tsx
// 胶囊类可点控件（2026-09-11 控件高度两档制，设计 §2）。全仓原来有 22 / 26 / 30 / 36 / 38 / 44 六种
// 胶囊高并存：状态胶囊 26、欢迎推荐 38、卡内「地图」26、「看菜单」「导航」22、地图页「全览」30。
// 这里只定一种：**视觉药丸 = `PILL`（泊车 36 / 行车 44），外框 = `TARGET`（48 / 56）**。
//  · testID / 无障碍 / 热区都在外框 `Pressable` 上——`target_probe` 量视觉 bounds，读到的仍是 ≥48 / 56；
//  · 视觉高只由 token 决定，站点不再各写一份 paddingVertical；
//  · 颜色四档 + `solid`（压在不可控内容上 ⇒ 实色底）+ `selected`（单选项）覆盖既有站点的全部用色。
//  · Android Visual v3（方向 B）：plain / accent / amber / floating 四档照 Figma Pill 组件——soft 底不描边，
//    floating = surfaceHigh 实色 + line 描边 + 二级投影（取代半透明 glass；过渡别名 `glass` 已于 v3 P7 删除）；
//    字重 500、左右 16；禁用 38%；**选中不再兼禁用**——已选中的单选项（如商户规格）原来被画成半透明。
// 按钮类（Dock 确认/取消、卡内按钮排、设置页按钮）不用它：那一档视觉就是外框本身（`TARGET`）。
import type { ReactNode } from 'react'
import { Pressable, Text, View, type AccessibilityRole, type AccessibilityState, type StyleProp, type ViewStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'
import type { Palette } from './theme'
import { PILL, RADIUS, TARGET, scale } from './tokens'

export type PillTone = 'plain' | 'accent' | 'amber' | 'floating'

export interface PillProps {
  p: Palette
  /** 文案；要放自定义内容（图标 + 文字、活动点）用 children，两者可并存（children 在前） */
  label?: string
  children?: ReactNode
  tone?: PillTone
  /** 单选项的选中态：accent 描边 + accentSoft 底，并进无障碍 `selected` */
  selected?: boolean
  /** 压在地图瓦片 / 设置文字上的浮层 ⇒ 实色底（map.tsx 既有判据：半透明底在瓦片上读不出来） */
  solid?: boolean
  /** 浮在别的内容上的胶囊带玻璃投影（状态胶囊 / 回到最新 / 支持页动作键） */
  elevated?: boolean
  /** 行车档：外框 56、药丸 44（§6「目标 ≥56dp」） */
  driving?: boolean
  fontScale?: FontScalePref
  onPress?: () => void
  disabled?: boolean
  testID?: string
  accessibilityLabel?: string
  accessibilityHint?: string
  accessibilityRole?: AccessibilityRole
  accessibilityState?: AccessibilityState
  accessibilityLiveRegion?: 'none' | 'polite' | 'assertive'
  /** 外框样式（对齐 / 收缩）；药丸自身的尺寸不接受覆盖 */
  style?: StyleProp<ViewStyle>
  /** 文字色覆盖（状态胶囊按 tone 着色文字） */
  textColor?: string
  /** 文字字号（pt，未 scale）；缺省 13 */
  fontSize?: number
  fontWeight?: '400' | '500' | '600' | '700'
  /** 水平内边距（dp）；缺省 16 */
  paddingHorizontal?: number
  numberOfLines?: number
  /** 文字最大宽（状态胶囊 260） */
  maxTextWidth?: number
}

/** 药丸的底 / 边 / 字色：一处决定，站点不各写一套。`border` 为 undefined = 不描边 */
export function pillColors(
  p: Palette,
  tone: PillTone,
  opts: { selected?: boolean; solid?: boolean } = {},
): { bg: string; border: string | undefined; fg: string } {
  const base: { bg: string; border: string | undefined; fg: string } = opts.selected
    ? { bg: p.accentSoft, border: p.accent, fg: p.accent }
    : tone === 'accent'
      ? { bg: p.accentSoft, border: undefined, fg: p.accent }
      : tone === 'amber'
        ? { bg: p.amberSoft, border: undefined, fg: p.amber }
        : tone === 'floating'
          ? { bg: p.surfaceHigh, border: p.line, fg: p.fg1 }
          : { bg: p.surfaceHighest, border: undefined, fg: p.fg1 }
  // 压在地图瓦片等不可控内容上：soft 底是半透明的，换成实色 surfaceHigh，补一圈 line 把边缘托出来
  return opts.solid ? { ...base, bg: p.surfaceHigh, border: base.border ?? p.line } : base
}

export function Pill({
  p,
  label,
  children,
  tone = 'plain',
  selected = false,
  solid = false,
  elevated = false,
  driving = false,
  fontScale = 'normal',
  onPress,
  disabled,
  testID,
  accessibilityLabel,
  accessibilityHint,
  accessibilityRole = 'button',
  accessibilityState,
  accessibilityLiveRegion,
  style,
  textColor,
  fontSize = 13,
  fontWeight = '500',
  paddingHorizontal = 16,
  numberOfLines = 1,
  maxTextWidth,
}: PillProps) {
  const frame = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const visual = scale(driving ? PILL.driving : PILL.parked, 'target', fontScale)
  const c = pillColors(p, tone, { selected, solid })
  return (
    <Pressable
      testID={testID}
      accessibilityRole={accessibilityRole}
      accessibilityLabel={accessibilityLabel}
      accessibilityHint={accessibilityHint}
      accessibilityLiveRegion={accessibilityLiveRegion}
      accessibilityState={{ ...(selected ? { selected: true } : {}), ...(disabled ? { disabled: true } : {}), ...accessibilityState }}
      onPress={onPress}
      disabled={disabled || !onPress}
      style={[{ minHeight: frame, justifyContent: 'center', alignSelf: 'flex-start' }, style]}
    >
      <View
        testID={testID ? `${testID}-pill` : undefined}
        style={{
          height: visual,
          paddingHorizontal,
          borderRadius: RADIUS.full,
          backgroundColor: c.bg,
          borderWidth: c.border ? 1 : 0,
          borderColor: c.border,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 6,
          // 选中的单选项即使不可再点也按选中画，不按禁用淡出
          opacity: disabled && !selected ? 0.38 : 1,
          boxShadow: elevated || tone === 'floating' ? p.elev2 : undefined,
        }}
      >
        {children}
        {label !== undefined ? (
          <Text
            numberOfLines={numberOfLines}
            style={{ color: textColor ?? c.fg, fontSize: scale(fontSize, 'text', fontScale), fontWeight, maxWidth: maxTextWidth }}
          >
            {label}
          </Text>
        ) : null}
      </View>
    </Pressable>
  )
}
