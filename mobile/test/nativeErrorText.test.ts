// 原生异常的人话（nativeErrorText.ts 头注）：只取最里层原因，没有包装原样返回。
import { nativeErrorText } from '@/core/voice/nativeErrorText'

test('Expo DecoratedException 包装 ⇒ 最里层原因', () => {
  expect(nativeErrorText(new Error("Call to function 'Kws.load' has been rejected.\n→ Caused by: 唤醒词引擎加载失败（诊断替身：强制失败一次）")))
    .toBe('唤醒词引擎加载失败（诊断替身：强制失败一次）')
  // 多层包装取最里层
  expect(nativeErrorText(new Error('outer\n→ Caused by: middle\n→ Caused by: inner'))).toBe('inner')
})

test('没有包装 / 非 Error / 空原因 ⇒ 不丢信息', () => {
  expect(nativeErrorText(new Error('录音权限未授予'))).toBe('录音权限未授予')
  expect(nativeErrorText('plain string')).toBe('plain string')
  expect(nativeErrorText(new Error('outer\n→ Caused by: '))).toBe('outer\n→ Caused by: ')
})
