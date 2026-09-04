// mobile/src/core/power/lowPower.ts
// 低电量材质回落判据（方案 §5.11 末句：reduce-transparency / 行车档 / 低电量 ⇒ 全部回落 G1-tint；B5-7 补第三种）。
// 纯函数、零 RN import。事实由 core/power/usePowerFacts 收集；原生缺席时两项为 null ⇒ 不回落（旧 APK 材质照旧）。
export const LOW_BATTERY_LEVEL = 0.2

export interface PowerFacts {
  /** 0–1；expo-battery 未知时 -1；原生缺席 null */
  level: number | null
  /** 系统省电模式；原生缺席 null */
  saver: boolean | null
}

export function lowPower(p: PowerFacts): boolean {
  if (p.saver) return true
  return p.level != null && p.level >= 0 && p.level < LOW_BATTERY_LEVEL
}
