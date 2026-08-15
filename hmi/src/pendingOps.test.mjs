import test from 'node:test'
import assert from 'node:assert/strict'

import {
  PENDING_CAPACITY, PENDING_TTL_MS,
  openPending, closePendings, prunePendings, isPendingLive,
} from './pendingOps.mjs'

test('容量与云端挂起表一致（前端留得比后端多 = 显示一条点下去必被拒的确认条）', () => {
  assert.equal(PENDING_CAPACITY, 3)
})

test('多条待确认并存，按 id 各自可见', () => {
  let ops = []
  for (const id of ['op-a', 'op-b']) ops = openPending(ops, id, 1000)
  assert.equal(isPendingLive(ops, 'op-a'), true)
  assert.equal(isPendingLive(ops, 'op-b'), true)
  assert.equal(isPendingLive(ops, 'op-zzz'), false)
})

test('超容量丢最旧一条（与后端 LRU 同向）', () => {
  let ops = []
  for (const id of ['op-a', 'op-b', 'op-c', 'op-d']) ops = openPending(ops, id, 1000)
  assert.deepEqual(ops.map((o) => o.id), ['op-b', 'op-c', 'op-d'])
})

test('同 id 重复下发是刷新不是新增', () => {
  let ops = openPending([], 'op-a', 1000)
  ops = openPending(ops, 'op-a', 2000)
  assert.equal(ops.length, 1)
  assert.equal(ops[0].ts, 2000)
})

test('关闭以服务端 closed 列表为准，只关点名的那几条', () => {
  let ops = []
  for (const id of ['op-a', 'op-b', 'op-c']) ops = openPending(ops, id, 1000)
  ops = closePendings(ops, ['op-b'])
  assert.deepEqual(ops.map((o) => o.id), ['op-a', 'op-c'])
})

test('空 closed 列表不动台账（插话轮不得撤掉还活着的确认条）', () => {
  const ops = openPending([], 'op-a', 1000)
  assert.equal(closePendings(ops, []), ops)
  assert.equal(closePendings(ops, undefined), ops)
})

test('本地限龄：超过 TTL 视为过期', () => {
  const ops = openPending([], 'op-a', 1000)
  assert.equal(prunePendings(ops, 1000 + PENDING_TTL_MS - 1).length, 1)
  assert.equal(prunePendings(ops, 1000 + PENDING_TTL_MS).length, 0)
})

test('空 id 不入账（位置授权征询等纯前端确认没有 operation_id）', () => {
  assert.deepEqual(openPending([], '', 1000), [])
  assert.equal(isPendingLive([{ id: 'op-a', ts: 1 }], ''), false)
})
