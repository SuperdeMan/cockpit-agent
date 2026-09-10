// 开发者选项的显隐判据（打磨批 B，评审 P17 / P18 / D1 / D2）。
// 只此一处：`developerOptionsVisible(settings, buildInfo)` = 非 prod 变体 ∨ 用户在构建行连点 7 次解锁。
// 与 `developmentDiagnosticsEnabled`（只认 dev 变体、管的是**会采集/播放/发送**的操作诊断）是两条判据：
// 解锁只打开只读的取证屏与画廊，永远打不开 dev 专属的操作诊断。
import { DEVELOPER_UNLOCK_TAPS, developerOptionsVisible, developmentDiagnosticsEnabled } from '@/core/diagnostics'
import { DEFAULT_APP_SETTINGS } from '@/core/settings/store'

const build = (variant: string) => ({ version: '0.1.0', variant, sha: 'abc', at: '', metro: false })

test('prod 未解锁 ⇒ 隐藏；prod 解锁 ⇒ 显示', () => {
  expect(developerOptionsVisible({ ...DEFAULT_APP_SETTINGS, developerUnlocked: false }, build('prod'))).toBe(false)
  expect(developerOptionsVisible({ ...DEFAULT_APP_SETTINGS, developerUnlocked: true }, build('prod'))).toBe(true)
})

test('非 prod 变体（dev / staging）不需要解锁', () => {
  for (const v of ['dev', 'staging']) {
    expect(developerOptionsVisible({ ...DEFAULT_APP_SETTINGS, developerUnlocked: false }, build(v))).toBe(true)
  }
})

test('缺省锁着：DEFAULT_APP_SETTINGS.developerUnlocked=false；解锁点数是 7', () => {
  expect(DEFAULT_APP_SETTINGS.developerUnlocked).toBe(false)
  expect(DEVELOPER_UNLOCK_TAPS).toBe(7)
})

test('解锁不等于 dev 变体：操作诊断（会采集 / 播放 / 发送）仍只认 dev', () => {
  // developmentDiagnosticsEnabled 读构建身份，不读设置——解锁改不了它（diagnosticRoutes.test 在屏上另验）
  expect(typeof developmentDiagnosticsEnabled()).toBe('boolean')
  expect(developerOptionsVisible({ ...DEFAULT_APP_SETTINGS, developerUnlocked: true }, build('prod'))).toBe(true)
})
