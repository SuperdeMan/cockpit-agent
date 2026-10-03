// mobile/src/ui/Segmented.tsx
// 分段按钮（Android Visual v3 · 方向 B）：设置页的单选（主题 / 字号 / 回答长度 / 识别方式 / 设备角色……）
// 从「一排 selected 胶囊」统一换成它。语义不变——仍是单选，选中项进无障碍 `selected`，testID 逐项沿用。
// 两档制：**视觉高 = PILL（36 / 44），每段的可点区 = TARGET（48 / 56）**。
// 可点区不能靠 hitSlop 撑：父容器一裁切（圆角要 overflow:hidden），Android 就收不到范围外的触摸——
// 所以每段本身就是 TARGET 高的 Pressable，药丸形的外框是叠在中间的一层、不接收触摸。
import { Pressable, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'
import { Icon, iconRuntimeAvailable } from './Icon'
import type { Palette } from './theme'
import { PILL, RADIUS, SPACE, TARGET, scale, textStyle } from './tokens'

export interface SegmentOption<T extends string> {
  value: T
  label: string
  testID?: string
  accessibilityLabel?: string
  disabled?: boolean
}

export interface SegmentedProps<T extends string> {
  p: Palette
  options: readonly SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  driving?: boolean
  fontScale?: FontScalePref
  testID?: string
  style?: StyleProp<ViewStyle>
}

export function Segmented<T extends string>({
  p,
  options,
  value,
  onChange,
  driving = false,
  fontScale = 'normal',
  testID,
  style,
}: SegmentedProps<T>) {
  const frame = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const visual = scale(driving ? PILL.driving : PILL.parked, 'target', fontScale)
  const inset = (frame - visual) / 2
  const last = options.length - 1
  return (
    <View testID={testID} style={[{ height: frame, flexDirection: 'row', alignSelf: 'stretch' }, style]}>
      {options.map((o, i) => {
        const on = o.value === value
        return (
          <Pressable
            key={o.value}
            testID={o.testID}
            accessibilityRole="button"
            accessibilityLabel={o.accessibilityLabel ?? o.label}
            accessibilityState={{ selected: on, ...(o.disabled ? { disabled: true } : {}) }}
            disabled={o.disabled}
            onPress={() => {
              if (!on) onChange(o.value)
            }}
            style={{ flex: 1, height: frame, justifyContent: 'center' }}
          >
            <View
              testID={o.testID ? `${o.testID}-segment` : undefined}
              style={{
                height: visual,
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                paddingHorizontal: SPACE[2],
                backgroundColor: on ? p.accentSoft : undefined,
                borderLeftWidth: i > 0 ? 1 : 0,
                borderColor: p.lineStrong,
                borderTopLeftRadius: i === 0 ? RADIUS.full : 0,
                borderBottomLeftRadius: i === 0 ? RADIUS.full : 0,
                borderTopRightRadius: i === last ? RADIUS.full : 0,
                borderBottomRightRadius: i === last ? RADIUS.full : 0,
                opacity: o.disabled ? 0.38 : 1,
              }}
            >
              {on && iconRuntimeAvailable() ? <Icon name="check" size={scale(16, 'text', fontScale)} color={p.accent} /> : null}
              <Text numberOfLines={1} style={[textStyle('labelM', fontScale), { color: on ? p.accent : p.fg2 }]}>
                {o.label}
              </Text>
            </View>
          </Pressable>
        )
      })}
      {/* 药丸外框：只画线、不接收触摸 */}
      <View
        pointerEvents="none"
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          top: inset,
          height: visual,
          borderRadius: RADIUS.full,
          borderWidth: 1,
          borderColor: p.lineStrong,
        }}
      />
    </View>
  )
}
