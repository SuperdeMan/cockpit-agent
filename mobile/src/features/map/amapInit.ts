// mobile/src/features/map/amapInit.ts
// 高德 SDK 初始化：**进程级一次**，所以守卫也放进程级（模块作用域），不是组件的 ref。
// 原来住在 map.tsx 里；舞台内嵌地图（StageMap）也要在渲染期调它 ⇒ 抽成一份，判据不复制。
//
// 隐私合规：高德 9.x 不调 updatePrivacyAgree/Show 就白屏（**且不报任何错**）。
// 库把这四个调用包在 initSDK 里，但外面套着 `apiKey?.let`——**必须把 key 传进去**，
// 传空等于整块不执行（2026-08-27 实测：地图灰屏、logcat 零输出，查了三轮才定位到）。
// ⚠ 它是**硬编码同意**（updatePrivacyShow(context, true, true)）——PoC 可以，
// 发布前必须有真实的隐私声明呈现（AM5-04 合规项，已挂账）。
//
// ⚠ **不能挪进 effect**：effect 在首帧 commit 之后才跑，那时 MapView 的原生视图已经建好了，
// 而高德要求 init 早于原生视图创建——挪过去的症状正是上面写的那种「白屏且零日志」。
// 幂等由模块级 `amapInited` 保证，所以「渲染期跑」在这里不会带来重复副作用。
import { AMapSdk } from 'react-native-amap3d'

import { MAP_AVAILABLE } from '@/core/map/available'

let amapInited = false

export function ensureAmapInit(key: string): void {
  if (!MAP_AVAILABLE || amapInited) return
  amapInited = true
  try {
    AMapSdk.init(key)
  } catch {
    /* 初始化失败下面照样渲染，白屏由用户可见地反馈，不静默 */
  }
}
