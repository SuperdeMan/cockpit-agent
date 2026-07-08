import test from 'node:test'
import assert from 'node:assert/strict'

import { RejectPolicy } from './rejectPolicy.mjs'

test('R4.4 P2：连续拒识 1 次无动作 / 2 次减半 / 3 次仅唤醒词', () => {
  const p = new RejectPolicy({ baseFollowupMs: 8000 })
  assert.equal(p.onRejected(), null)                              // #1 只计数
  assert.equal(p.streak, 1)
  assert.deepEqual(p.onRejected(), { type: 'tighten', followupMs: 4000 })  // #2 减半
  assert.deepEqual(p.onRejected(), { type: 'wake_only' })         // #3 仅唤醒词
  assert.equal(p.streak, 3)
  assert.deepEqual(p.onRejected(), { type: 'wake_only' })         // #4 保持仅唤醒词
})

test('R4.4 P2：一次成功交互复位并清零，再拒从 1 重计', () => {
  const p = new RejectPolicy({ baseFollowupMs: 8000 })
  p.onRejected(); p.onRejected(); p.onRejected()                  // 降级到 wake_only
  assert.deepEqual(p.onAccepted(), { type: 'restore', followupMs: 8000 })
  assert.equal(p.streak, 0)
  // 无连续拒识时 onAccepted 无动作
  assert.equal(p.onAccepted(), null)
  // 再拒从 1 重计（不接着上次）
  assert.equal(p.onRejected(), null)
  assert.equal(p.streak, 1)
})

test('R4.4 P2：基准续问窗随设置同步，restore 以最新设置为准', () => {
  const p = new RejectPolicy({ baseFollowupMs: 8000 })
  p.setBaseFollowupMs(6000)                                       // 用户把续问窗改成 6s
  assert.deepEqual(p.onRejected(), null)
  assert.deepEqual(p.onRejected(), { type: 'tighten', followupMs: 3000 })  // 6000/2
  assert.deepEqual(p.onAccepted(), { type: 'restore', followupMs: 6000 })  // 还原到最新设置
})
