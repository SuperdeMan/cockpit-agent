// mobile/src/ui/Sheet.tsx
// 底部弹层与对话框的面板（Android Visual v3 · 方向 B，G0 实色）：只管「长什么样」，不管显隐与遮罩点按——
// 隐私栏、端到端同意页、待处理列表各自已有 Modal / 遮罩判据（谁能关、点遮罩算不算拒绝），这里不改那些语义。
//  · SheetPanel：surfaceHigh 不透明、顶角 28、三级投影；把手 32×4；标题行（title/l）+ 可选关闭键；内容；可选底部按钮。
//  · DialogPanel：宽 ≤312、圆角 28、内边距 24；标题 title/l + 正文 bodyM；按钮右对齐。
import type { ReactNode } from 'react'
import { Pressable, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'
import { Icon, iconRuntimeAvailable } from './Icon'
import type { Palette } from './theme'
import { RADIUS, SPACE, TARGET, scale, textStyle } from './tokens'

export interface SheetPanelProps {
  p: Palette
  title?: string
  /** 给了才出右上角关闭键 */
  onClose?: () => void
  closeLabel?: string
  closeTestID?: string
  children: ReactNode
  /** 底部按钮行（通常是 Button） */
  actions?: ReactNode
  fontScale?: FontScalePref
  testID?: string
  style?: StyleProp<ViewStyle>
}

export function SheetPanel({ p, title, onClose, closeLabel = '关闭', closeTestID, children, actions, fontScale = 'normal', testID, style }: SheetPanelProps) {
  const hit = scale(TARGET.parked, 'target', fontScale)
  return (
    <View
      testID={testID}
      style={[
        {
          backgroundColor: p.surfaceHigh,
          borderTopLeftRadius: RADIUS['3xl'],
          borderTopRightRadius: RADIUS['3xl'],
          boxShadow: p.elev3,
          paddingBottom: SPACE[4],
        },
        style,
      ]}
    >
      <View style={{ height: SPACE[4], alignItems: 'center', justifyContent: 'center' }}>
        <View style={{ width: 32, height: 4, borderRadius: 2, backgroundColor: p.lineStrong }} />
      </View>
      {title || onClose ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', paddingLeft: SPACE[4], paddingRight: SPACE[2], minHeight: hit }}>
          <Text accessibilityRole="header" style={[textStyle('titleL', fontScale), { flex: 1, color: p.fg1 }]}>
            {title}
          </Text>
          {onClose ? (
            <Pressable
              testID={closeTestID}
              accessibilityRole="button"
              accessibilityLabel={closeLabel}
              onPress={onClose}
              style={{ width: hit, height: hit, alignItems: 'center', justifyContent: 'center' }}
            >
              {iconRuntimeAvailable() ? (
                <Icon name="close" size={scale(20, 'text', fontScale)} color={p.fg2} />
              ) : (
                <Text style={[textStyle('labelL', fontScale), { color: p.accent }]}>{closeLabel}</Text>
              )}
            </Pressable>
          ) : null}
        </View>
      ) : null}
      <View style={{ paddingHorizontal: SPACE[4], flexShrink: 1 }}>{children}</View>
      {actions ? <View style={{ paddingHorizontal: SPACE[4], paddingTop: SPACE[2], gap: SPACE[1] }}>{actions}</View> : null}
    </View>
  )
}

export interface DialogPanelProps {
  p: Palette
  title: string
  message?: string
  children?: ReactNode
  /** 右对齐的按钮行（通常是 text / destructive 样式的 Button） */
  actions: ReactNode
  fontScale?: FontScalePref
  testID?: string
}

export function DialogPanel({ p, title, message, children, actions, fontScale = 'normal', testID }: DialogPanelProps) {
  return (
    <View
      testID={testID}
      accessibilityViewIsModal
      style={{
        width: '100%',
        maxWidth: 312,
        alignSelf: 'center',
        backgroundColor: p.surfaceHigh,
        borderRadius: RADIUS['3xl'],
        boxShadow: p.elev3,
        paddingHorizontal: SPACE[4],
        paddingTop: SPACE[4],
        paddingBottom: SPACE[3],
        gap: SPACE[3],
      }}
    >
      <Text accessibilityRole="header" style={[textStyle('titleL', fontScale), { color: p.fg1 }]}>
        {title}
      </Text>
      {message ? <Text style={[textStyle('bodyM', fontScale), { color: p.fg2 }]}>{message}</Text> : null}
      {children}
      <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: SPACE[1] }}>{actions}</View>
    </View>
  )
}
