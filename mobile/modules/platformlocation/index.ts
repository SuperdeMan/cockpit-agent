// 系统定位原生模块的 JS 面（2026-09-17）。**只透传，不判定**——取值策略在 src/core/location/fixPolicy.ts。
//
// 为什么有它：expo-location 只走 GMS FusedLocationProviderClient。真机（OPPO PEUM00）`dumpsys location`：
// GMS 的 fused 缓存三天没动（一直是公司那一点），HIGH_ACCURACY 请求只挂 gps provider、室内永远拿不到；
// 而系统的 network provider 每 5 分钟都有新定位（sceneservice / 系统自己在要）。昨晚在家问「我在哪」答的是
// 公司地址，就是读了 GMS 那份三天前的缓存——系统缓存里 5 分钟前的家里坐标它看不见。
//
// ⚠ 原生缺席时 requireOptionalNativeModule 返回 null 而不是抛（坑账 §9.27 铁则）：旧 APK 上 JS 走 expo-location 那条老路。
import { requireOptionalNativeModule } from 'expo'

export interface PlatformFix {
  /** gps | network | fused | passive */
  provider: string
  latitude: number
  longitude: number
  /** 米；provider 没给就是 null */
  accuracy: number | null
  /** 定位产生的墙钟时刻（ms） */
  time: number
  /** 定位产生到现在的**单调时钟**年龄（ms）——判新鲜度用它，不用 time（墙钟可能被拨过） */
  ageMs: number
}

interface PlatformLocationNativeModule {
  /** 各 provider 的 last-known；没授权 / 没 provider ⇒ 空数组 */
  lastKnown(): PlatformFix[]
  /** network + gps 各要一次新定位，先到先用；到点 / 没授权 / API<30 ⇒ null */
  requestFix(timeoutMs: number): Promise<PlatformFix | null>
}

const native = requireOptionalNativeModule<PlatformLocationNativeModule>('PlatformLocation')

/** 原生模块在不在场。false = 这个 APK 没带它，定位取值退回 expo-location（GMS）那条路。 */
export const PLATFORM_LOCATION_AVAILABLE = native != null

export default native
