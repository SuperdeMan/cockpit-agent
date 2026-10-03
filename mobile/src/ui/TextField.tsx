// mobile/src/ui/TextField.tsx
// 输入框（Android Visual v3 · 方向 B）：标签 labelM + 输入框（高 = TARGET、圆角 12、surfaceHigh 底、line 描边）
// + 说明行 caption。聚焦 = 2px accent；出错 = 2px red + 说明行变红（错误文字同时进无障碍标签）；禁用 38%。
// 引导页的域名 / token、设置页的昵称用它；对话页的 Composer 输入框是另一套（手势契约），不走这里。
import { useState } from 'react'
import { Text, TextInput, View, type StyleProp, type TextInputProps, type ViewStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'
import type { Palette } from './theme'
import { RADIUS, SPACE, TARGET, scale, textStyle } from './tokens'

export interface TextFieldProps extends Omit<TextInputProps, 'style' | 'editable'> {
  p: Palette
  label: string
  /** 说明行（出错时被 error 替换） */
  helper?: string
  /** 出错文案；有值即出错态 */
  error?: string
  disabled?: boolean
  driving?: boolean
  fontScale?: FontScalePref
  style?: StyleProp<ViewStyle>
}

export function TextField({
  p,
  label,
  helper,
  error,
  disabled = false,
  driving = false,
  fontScale = 'normal',
  style,
  testID,
  accessibilityLabel,
  onFocus,
  onBlur,
  ...input
}: TextFieldProps) {
  const [focused, setFocused] = useState(false)
  const height = scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const border = error ? p.red : focused ? p.accent : p.line
  const note = error ?? helper
  return (
    <View style={[{ gap: 6, opacity: disabled ? 0.38 : 1 }, style]}>
      <Text style={[textStyle('labelM', fontScale), { color: p.fg2 }]}>{label}</Text>
      <TextInput
        {...input}
        testID={testID}
        editable={!disabled}
        accessibilityLabel={accessibilityLabel ?? (error ? `${label}，${error}` : label)}
        onFocus={(e) => {
          setFocused(true)
          onFocus?.(e)
        }}
        onBlur={(e) => {
          setFocused(false)
          onBlur?.(e)
        }}
        placeholderTextColor={p.fg3}
        style={[
          textStyle('bodyM', fontScale),
          {
            minHeight: height,
            paddingHorizontal: SPACE[3],
            borderRadius: RADIUS.md,
            backgroundColor: p.surfaceHigh,
            borderWidth: error || focused ? 2 : 1,
            borderColor: border,
            color: p.fg1,
          },
        ]}
      />
      {note ? (
        <Text testID={testID ? `${testID}-note` : undefined} style={[textStyle('caption', fontScale), { color: error ? p.red : p.fg3 }]}>
          {note}
        </Text>
      ) : null}
    </View>
  )
}
