// 构建身份（常驻包流程，2026-09-07）：设置页底部一行「这台手机跑的是哪一份包」。
// 来源是 app.config.ts::extra——variant 由 APP_VARIANT 决定，sha / at 由 scripts/build_mobile.ps1
// 注入；Metro 开发态（__DEV__，dev-client 连 Metro）没有构建脚本注入 ⇒ 显示 Metro，
// 别把它读成「没打上 SHA」。真机读数登记 SHA 时以这一行为准（实施计划坑账 §9.81：
// 先证明设备跑的是本轮代码，再谈修没修好）。
import Constants from 'expo-constants'

export interface BuildInfo {
  version: string
  variant: string
  sha: string
  at: string
  /** Metro 开发态：JS 来自 Metro 而不是包内 bundle */
  metro: boolean
}

export function readBuildInfo(): BuildInfo {
  const cfg = Constants.expoConfig
  const extra = (cfg?.extra ?? {}) as { variant?: string; build?: { sha?: string; at?: string } }
  return {
    version: cfg?.version ?? '?',
    variant: extra.variant ?? '?',
    sha: extra.build?.sha ?? '',
    at: extra.build?.at ?? '',
    metro: typeof __DEV__ !== 'undefined' && __DEV__,
  }
}

/** 例：`v0.1.0 · prod · a203499f1 · 2026-09-07 01:20`；Metro 开发态：`v0.1.0 · dev · Metro` */
export function formatBuildLabel(b: BuildInfo): string {
  const parts = [`v${b.version}`, b.variant]
  if (b.metro) parts.push('Metro')
  if (b.sha) parts.push(b.sha)
  if (b.at) parts.push(b.at)
  return parts.join(' · ')
}
