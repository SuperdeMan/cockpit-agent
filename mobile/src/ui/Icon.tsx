// 座舱线性图标 RN 版（Aurora Glass 复刻轮）：数据共享 hmi 的 A-8 图标库
// （hmi/src/components 的 icons.gen.ts + icons.custom.ts，纯 {w,h,body} 数据，台账已登记），
// 渲染走 react-native-svg 的 SvgXml——与 hmi Icon.tsx 同一份 24×24 居中/1.8 stroke 契约。
// 4 态里只实现 default/active/disabled（色值由调用方从 Palette 取）；aiMoment 极光渐变描边
// 本端暂无消费点，刻意不实现（要用时照 hmi Icon.tsx 的 AURORA_GRAD 内联 defs）。
// ⚠ svg 是原生支撑组件（坑账 §9.27）：原生缺席时 Fabric 在挂载期原生线程抛、
// ErrorBoundary 兜不住 ⇒ 渲染前必须探 TurboModuleRegistry，缺席回退由调用方给文字。
import { TurboModuleRegistry } from 'react-native'
import { SvgXml } from 'react-native-svg'

import { ICON_CUSTOM } from '@shared/components/icons.custom.ts'
import { ICON_DATA, type IconName as GenIconName } from '@shared/components/icons.gen.ts'

const REGISTRY: Record<string, { w: number; h: number; body: string }> = { ...ICON_DATA, ...ICON_CUSTOM }

export type IconName = GenIconName | keyof typeof ICON_CUSTOM

let probed: boolean | null = null
/** svg 原生在场探测（merchantCards.tsx 同款判据）；结果进程内缓存 */
export function iconRuntimeAvailable(): boolean {
  if (probed === null) {
    try {
      probed = TurboModuleRegistry.get('RNSVGSvgViewModule') != null
    } catch {
      probed = false
    }
  }
  return probed
}

export function Icon({ name, size = 20, color }: { name: IconName; size?: number; color: string }) {
  const d = REGISTRY[name]
  if (!d || !iconRuntimeAvailable()) return null
  const tx = ((24 - d.w) / 2).toFixed(2)
  const ty = ((24 - d.h) / 2).toFixed(2)
  const xml =
    `<svg viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.8" ` +
    `stroke-linecap="round" stroke-linejoin="round">` +
    `<g transform="translate(${tx} ${ty})">${d.body.replace(/currentColor/g, color)}</g></svg>`
  return <SvgXml xml={xml} width={size} height={size} />
}
