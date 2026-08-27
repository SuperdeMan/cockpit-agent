// 液态玻璃容器（aurora.css .au-glass 的 RN 版）：半透明底 + 四边不等光照边框
// （上左亮下右暗=光源方向感）+ inset 顶缘高光 + 大投影。RN 无 backdrop-filter，
// 玻璃感靠叠在 AuroraBackground 深空渐变上的半透明底（aurora.css --au-glass-fallback 同路线）。
import type { ReactNode } from 'react'
import { View, type StyleProp, type ViewStyle } from 'react-native'

import type { Palette } from '../theme'

export function Glass({
  p,
  r = 20,
  style,
  children,
}: {
  p: Palette
  r?: number
  style?: StyleProp<ViewStyle>
  children?: ReactNode
}) {
  return (
    <View
      style={[
        {
          backgroundColor: p.glassBg,
          borderRadius: r,
          borderTopWidth: 1,
          borderLeftWidth: 1,
          borderRightWidth: 1,
          borderBottomWidth: 1,
          borderTopColor: p.glassBdTop,
          borderLeftColor: p.glassBdLeft,
          borderRightColor: p.glassBdRight,
          borderBottomColor: p.glassBdBottom,
          boxShadow: p.glassShadow,
        },
        style,
      ]}
    >
      {children}
    </View>
  )
}
