// mobile/test/activityLog.test.ts
// 隐私栏「最近一次激活」的数据源（方案 §5.10）：20 条内存环形日志，**不上传、不持久化**。
import { ActivityLog } from '@/core/presence/activityLog'

test('环形 20 条，最新在前', () => {
  const log = new ActivityLog(20, () => 1000)
  for (let i = 0; i < 25; i += 1) log.push('mic', `唤醒词命中 ${i}`)
  const items = log.list()
  expect(items).toHaveLength(20)
  expect(items[0].note).toBe('唤醒词命中 24')
  expect(items[19].note).toBe('唤醒词命中 5')
})

test('lastOf 取该来源最近一条；没有返回 null', () => {
  let t = 0
  const log = new ActivityLog(20, () => (t += 1000))
  log.push('mic', '按住说话')
  log.push('camera', '触发词「这是什么」')
  log.push('mic', '唤醒词命中')
  expect(log.lastOf('mic')).toMatchObject({ note: '唤醒词命中', at: 3000 })
  expect(log.lastOf('camera')).toMatchObject({ note: '触发词「这是什么」' })
  expect(log.lastOf('location')).toBeNull()
})

test('订阅：每次 push 通知一次；退订后不再收', () => {
  const log = new ActivityLog(5, () => 0)
  let n = 0
  const off = log.subscribe(() => (n += 1))
  log.push('mic', 'a')
  log.push('mic', 'b')
  off()
  log.push('mic', 'c')
  expect(n).toBe(2)
})
