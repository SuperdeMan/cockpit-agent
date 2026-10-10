import test from 'node:test'
import assert from 'node:assert/strict'

import { dropRejectedTurn, rejectedNotice } from './rejectedTurn.mjs'

const msgs = [
  { id: 'u1', role: 'user', text: '深圳明天天气怎么样' },
  { id: 'a1', role: 'assistant', text: '深圳明天多云。' },
  { id: 'u2', role: 'user', text: '你吃饭了没有', provisional: true },
  { id: 'a2', role: 'assistant', text: '', pending: true },
]

test('被拒的一轮整轮删掉：紧挨在占位前面的用户话一起删（HMI 口径）', () => {
  assert.deepEqual(dropRejectedTurn(msgs, 'a2').map((m) => m.id), ['u1', 'a1'])
})

test('给了用户气泡 id 就按 id 删（Android：草稿气泡转正后中间可能夹着别的消息）', () => {
  const withProactive = [...msgs.slice(0, 3), { id: 'p1', role: 'assistant', text: '💡 前方拥堵' }, msgs[3]]
  assert.deepEqual(dropRejectedTurn(withProactive, 'a2', 'u2').map((m) => m.id), ['u1', 'a1', 'p1'])
})

test('前面不是用户话就只删占位；找不到占位原样返回、不误删', () => {
  const lone = [{ id: 'a0', role: 'assistant', text: '' }, ...msgs.slice(0, 2)]
  assert.deepEqual(dropRejectedTurn(lone, 'a0').map((m) => m.id), ['u1', 'a1'])
  assert.equal(dropRejectedTurn(msgs, 'missing'), msgs)
  assert.deepEqual(dropRejectedTurn(msgs, 'a2', 'a1').map((m) => m.id), ['u1', 'a1', 'u2'], 'id 指向的不是用户话就不删它')
})

test('提示带唤醒词；唤醒词关着就不提', () => {
  assert.equal(rejectedNotice('小舟小舟'), '刚才那句不像是对我说的，已忽略；需要我时先喊「小舟小舟」')
  assert.equal(rejectedNotice(''), '刚才那句不像是对我说的，已忽略')
})
