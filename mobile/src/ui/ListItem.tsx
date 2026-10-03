// mobile/src/ui/ListItem.tsx
// 设置类列表（Android Visual v3 · 方向 B）：分组 = surface 底、圆角 16 的容器，行间 line 分隔；
// 行 = 最小高 TARGET、左右 16、上下 12；标题 bodyM + 可选说明 caption；右槽放 Switch / 值 + 箭头；
// 分段单选放在行下（`below`）。Kind 只决定标题颜色：link = accent、danger = red。
// 交互不在这里新增：行可点当且仅当调用方给了 onPress（Switch 行的开关仍是 Switch 自己的手势）。
import { Children, Fragment, isValidElement, type ReactNode } from 'react'
import { Pressable, Text, View, type StyleProp, type SwitchProps, type ViewStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'
import { Icon, iconRuntimeAvailable } from './Icon'
import type { Palette } from './theme'
import { RADIUS, SPACE, TARGET, scale, textStyle } from './tokens'

export type ListItemKind = 'plain' | 'link' | 'danger'

export interface ListItemProps {
  p: Palette
  title: string
  subtitle?: string
  kind?: ListItemKind
  /** 右侧的值文字（「手持」）；有 onPress 时后面跟一个箭头 */
  value?: string
  /** 右槽（Switch、按钮）；与 value 二选一 */
  right?: ReactNode
  /** 行下内容（分段单选、输入框） */
  below?: ReactNode
  onPress?: () => void
  disabled?: boolean
  driving?: boolean
  fontScale?: FontScalePref
  testID?: string
  accessibilityLabel?: string
  accessibilityHint?: string
}

export function ListItem({
  p,
  title,
  subtitle,
  kind = 'plain',
  value,
  right,
  below,
  onPress,
  disabled = false,
  driving = false,
  fontScale = 'normal',
  testID,
  accessibilityLabel,
  accessibilityHint,
}: ListItemProps) {
  const minHeight = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const titleColor = kind === 'danger' ? p.red : kind === 'link' ? p.accent : p.fg1
  const body = (
    <View style={{ gap: 10, opacity: disabled ? 0.38 : 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: SPACE[3] }}>
        <View style={{ flex: 1, gap: 2 }}>
          <Text style={[textStyle('bodyM', fontScale), { color: titleColor }]}>{title}</Text>
          {subtitle ? <Text style={[textStyle('caption', fontScale), { color: p.fg3 }]}>{subtitle}</Text> : null}
        </View>
        {value !== undefined ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            <Text style={[textStyle('bodyM', fontScale), { color: p.fg2 }]}>{value}</Text>
            {onPress && iconRuntimeAvailable() ? <Icon name="chevron-right" size={scale(16, 'text', fontScale)} color={p.fg3} /> : null}
          </View>
        ) : null}
        {right}
      </View>
      {below}
    </View>
  )
  const frame: ViewStyle = { minHeight, justifyContent: 'center', paddingHorizontal: SPACE[3], paddingVertical: SPACE[2] }
  if (!onPress) {
    return (
      <View testID={testID} accessibilityLabel={accessibilityLabel} style={frame}>
        {body}
      </View>
    )
  }
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={accessibilityHint}
      accessibilityState={disabled ? { disabled: true } : undefined}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [frame, pressed ? { backgroundColor: p.fill2 } : null]}
    >
      {body}
    </Pressable>
  )
}

/** 分组容器：surface 底、圆角 16、行间 line 分隔（null / false 子项不占分隔线） */
export function ListGroup({ p, children, testID, style }: { p: Palette; children: ReactNode; testID?: string; style?: StyleProp<ViewStyle> }) {
  const items = Children.toArray(children).filter(isValidElement)
  return (
    <View testID={testID} style={[{ backgroundColor: p.surface, borderRadius: RADIUS.lg, overflow: 'hidden' }, style]}>
      {items.map((child, i) => (
        <Fragment key={child.key ?? i}>
          {i > 0 ? <View style={{ height: 1, backgroundColor: p.line, marginLeft: SPACE[3] }} /> : null}
          {child}
        </Fragment>
      ))}
    </View>
  )
}

/** 分区标题：labelM、accent 色，上 16 下 8 */
export function ListSectionHeader({ p, title, fontScale = 'normal', testID }: { p: Palette; title: string; fontScale?: FontScalePref; testID?: string }) {
  return (
    <Text
      testID={testID}
      accessibilityRole="header"
      style={[textStyle('labelM', fontScale), { color: p.accent, paddingHorizontal: SPACE[3], paddingTop: SPACE[3], paddingBottom: SPACE[1] }]}
    >
      {title}
    </Text>
  )
}

/** 原生 Switch 的配色（Figma Switch 组件）：开 = accent 轨道 + onAccent 滑块；关 = surfaceHighest 轨道 + fg3 滑块。
 *  原生 Switch 画不了关闭态的 2px 描边，Android 上靠轨道与底色的色差区分 */
export function switchColors(p: Palette, on: boolean): Pick<SwitchProps, 'trackColor' | 'thumbColor' | 'ios_backgroundColor'> {
  return {
    trackColor: { false: p.surfaceHighest, true: p.accent },
    thumbColor: on ? p.onAccent : p.fg3,
    ios_backgroundColor: p.surfaceHighest,
  }
}
