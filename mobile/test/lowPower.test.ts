// mobile/test/lowPower.test.ts
// 低电量材质回落判据（方案 §5.11 末句第三种回落；B5-7）。事实来自 expo-battery，原生缺席时两项都是 null。
import { LOW_BATTERY_LEVEL, lowPower } from '@/core/power/lowPower'

describe('lowPower', () => {
  test('省电模式 ⇒ 回落，不看电量', () => {
    expect(lowPower({ level: 0.9, saver: true })).toBe(true)
  })
  test('电量低于阈值 ⇒ 回落；等于阈值不回落', () => {
    expect(lowPower({ level: LOW_BATTERY_LEVEL - 0.01, saver: false })).toBe(true)
    expect(lowPower({ level: LOW_BATTERY_LEVEL, saver: false })).toBe(false)
  })
  test('原生缺席（null / null）⇒ 不回落——旧 APK 上材质照旧', () => {
    expect(lowPower({ level: null, saver: null })).toBe(false)
  })
  test('电量未知（expo-battery 用 -1 表示）⇒ 不回落', () => {
    expect(lowPower({ level: -1, saver: false })).toBe(false)
  })
  test('阈值是 20%（§5.11 没给数，取 Android 省电提示的默认档）', () => {
    expect(LOW_BATTERY_LEVEL).toBe(0.2)
  })
})
