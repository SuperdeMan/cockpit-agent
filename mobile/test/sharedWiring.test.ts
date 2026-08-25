// 共享接线冒烟（实施计划 M0-3）：@shared/* 别名 + .mjs 解析在 jest（babel 转译）侧走通。
// 真机 dev-client 侧的同款验证（console 打印退避值）待 E3 真机接入后在调试屏补做。
import { appendToken, nextBackoff } from '@shared/ws.mjs'

test('nextBackoff：指数退避 + 抖动，封顶 max', () => {
  // attempt=3, min=1000 → base=8000，rand=0.5 → +2000
  expect(nextBackoff(3, 1000, 30000, () => 0.5)).toBe(10000)
  // 封顶：attempt 很大时 ≤ max
  expect(nextBackoff(20, 1000, 30000, () => 0.99)).toBeLessThanOrEqual(30000)
})

test('appendToken：urlencode 后拼 ?token=', () => {
  expect(appendToken('wss://h.ts.net:8443/ws', 'a b')).toBe('wss://h.ts.net:8443/ws?token=a%20b')
  expect(appendToken('wss://h/ws?x=1', 't')).toBe('wss://h/ws?x=1&token=t')
  expect(appendToken('wss://h/ws', '')).toBe('wss://h/ws')
})
