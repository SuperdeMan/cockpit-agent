import test from 'node:test'
import assert from 'node:assert/strict'
import { visibleQuickCommands } from './quickCommands.mjs'

// 声明表由调用方注入（生产里来自 types.ts 的 SYSTEM_QUICK_COMMAND_AGENTS / AGENT_CATALOG）；
// 这里用一份等价的小表，只验判据本身。
const BINDINGS = {
  '打开空调26度': 'vehicle',
  '播放音乐': 'media',
  '今天天气怎么样': 'info',
  '讲个笑话': 'chitchat',
}
const SERVER_ID = { vehicle: 'edge-vehicle', media: 'edge-media' }
const serverIdOf = (id) => SERVER_ID[id] || id
const ALL = Object.keys(BINDINGS)
const complete = (ids) => ({
  summaryStatus: 'complete',
  capabilities: ids.map((id) => ({ id, status: 'available' })),
})
const pick = (commands, summary, switches = {}) =>
  visibleQuickCommands(commands, summary, switches, BINDINGS, serverIdOf)

test('没有车控授权时，车控推荐不再摆在首页', () => {
  const out = pick(ALL, complete(['chitchat']))
  assert.equal(out.includes('打开空调26度'), false)
  assert.equal(out.includes('讲个笑话'), true)
})

test('有车控授权时车控推荐照常在（按服务端 agent_id 对账）', () => {
  assert.equal(pick(ALL, complete(['edge-vehicle'])).includes('打开空调26度'), true)
  // 用 UI id 而不是服务端 id 时对不上——这正是要写出 serverId 的原因
  assert.equal(pick(ALL, complete(['vehicle'])).includes('打开空调26度'), false)
})

test('摘要只取到一半时不筛——「此刻查不到」不许显示成「你没有这个功能」', () => {
  assert.deepEqual(pick(ALL, { summaryStatus: 'partial', capabilities: [] }), ALL)
})

test('压根没有摘要时也不筛（旧服务端 / 还没查）', () => {
  assert.deepEqual(pick(ALL, null), ALL)
})

test('用户自定义短语永远保留——系统无权替他判定用不了', () => {
  assert.deepEqual(pick(['帮我把车开到月球'], complete([])), ['帮我把车开到月球'])
})

test('用户自己关掉的能力不再推荐，但传入的列表不被改动', () => {
  const input = [...ALL]
  const out = pick(input, complete(['edge-vehicle']), { vehicle: false })
  assert.equal(out.includes('打开空调26度'), false)
  assert.deepEqual(input, ALL)
})

test('完整摘要里没出现的能力按不可用处理', () => {
  assert.deepEqual(pick(['今天天气怎么样'], complete(['chitchat'])), [])
})

test('非法输入不抛', () => {
  assert.deepEqual(visibleQuickCommands(null, null, undefined, undefined), [])
  assert.deepEqual(visibleQuickCommands(['', 1, '讲个笑话'], null, undefined, BINDINGS), ['讲个笑话'])
})
