// 深空场景底（shell.css .au-scene-bg 逐值）：155° 深空渐变 + 三个极光氛围 blob，
// 玻璃面板悬浮其上。blob 用 radial 透明衰减替代 web 的 filter:blur(70~80px)——
// 渐变自带柔边，全机型一致且零滤镜开销。浅色主题 blob 减半透明度（web 同款 opacity:.5）。
// 静态层零动画：氛围感来自玻璃面板叠在它上面的折射错觉，不来自运动。
import { View } from 'react-native'

import type { Palette } from '../theme'

export function AuroraBackground({ p }: { p: Palette }) {
  const dim = p.dark ? 1 : 0.5
  return (
    <View pointerEvents="none" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, overflow: 'hidden' }}>
      <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, experimental_backgroundImage: p.sceneGradient }} />
      {/* b1 蓝 · 左上 */}
      <View
        style={{
          position: 'absolute', top: '-12%', left: '-14%', width: 460, height: 460, borderRadius: 9999,
          opacity: dim,
          experimental_backgroundImage: 'radial-gradient(circle, rgba(91,140,255,0.30) 0%, rgba(91,140,255,0) 70%)',
        }}
      />
      {/* b2 紫 · 右下 */}
      <View
        style={{
          position: 'absolute', bottom: '-14%', right: '-16%', width: 520, height: 520, borderRadius: 9999,
          opacity: dim,
          experimental_backgroundImage: 'radial-gradient(circle, rgba(154,107,255,0.24) 0%, rgba(154,107,255,0) 70%)',
        }}
      />
      {/* b3 青 · 中右 */}
      <View
        style={{
          position: 'absolute', top: '30%', right: '18%', width: 320, height: 320, borderRadius: 9999,
          opacity: dim,
          experimental_backgroundImage: 'radial-gradient(circle, rgba(91,233,255,0.14) 0%, rgba(91,233,255,0) 70%)',
        }}
      />
    </View>
  )
}
