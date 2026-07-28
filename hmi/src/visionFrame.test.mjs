// 视觉单帧单测（M4 P4）：触发词收窄 / guard 让路 / 失败不打断 / 用完关摄像头。
import test from 'node:test'
import assert from 'node:assert/strict'
import { needsFrame, captureFrame } from './visionFrame.mjs'

// ── 触发判定（与 agents/vision/manifest.yaml 的 route_hints 同口径）──

test('典型视觉句命中', () => {
  for (const t of ['那是什么', '这是什么', '前面那个建筑是什么', '这是什么车',
    '路边那个是什么花', '看看这个', '帮我看看前面', '那是什么牌子的车']) {
    assert.equal(needsFrame(t), true, t)
  }
})

test('POI 详情句让路（归 nearby，不该抓帧）', () => {
  for (const t of ['这家怎么样', '那家评分多少', '第二个的详情', '这家几点营业']) {
    assert.equal(needsFrame(t), false, t)
  }
})

test('LBS 句不抓帧（数据飞轮 P0 收窄：「帮我看看」单独出现≠视觉意图）', () => {
  // badcase 2026-07-26/27：过宽分支「帮我看看」曾让这些句触发抓帧+上传并劫持路由
  for (const t of ['帮我看看附近有什么咖啡店', '帮我看看我附近有什么好吃的',
    '帮我看看路况']) {
    assert.equal(needsFrame(t), false, t)
  }
})

test('普通句不抓帧（默认一帧都不采）', () => {
  for (const t of ['今天天气怎么样', '打开空调', '导航去公司', '播放音乐', '']) {
    assert.equal(needsFrame(t), false, t)
  }
})

test('空/空白输入不抓帧', () => {
  assert.equal(needsFrame(''), false)
  assert.equal(needsFrame('   '), false)
  assert.equal(needsFrame(null), false)
})

// ── 抓帧上传 ──

function fakeStream() {
  const stopped = []
  return {
    stream: { getTracks: () => [{ stop: () => stopped.push(1) }] },
    stopped,
  }
}

test('抓帧成功 → 返回 frame_id', async () => {
  const { stream } = fakeStream()
  global.fetch = async () => ({ ok: true, json: async () => ({ frame_id: 'vf_abc' }) })
  const id = await captureFrame('http://x', {
    getStream: async () => stream, grab: async () => new Uint8Array([1, 2, 3]),
  })
  assert.equal(id, 'vf_abc')
})

test('上传失败 → 空串（由 Agent 侧诚实说没拿到画面，不在这里弹错打断对话）', async () => {
  const { stream } = fakeStream()
  global.fetch = async () => ({ ok: false, status: 500 })
  const id = await captureFrame('http://x', {
    getStream: async () => stream, grab: async () => new Uint8Array([1]),
  })
  assert.equal(id, '')
})

test('抓不到画面 → 空串，不抛出', async () => {
  const { stream } = fakeStream()
  global.fetch = async () => { throw new Error('should not be called') }
  const id = await captureFrame('http://x', {
    getStream: async () => stream, grab: async () => null,
  })
  assert.equal(id, '')
})

test('注入的流不由我们关闭（复用既有 mic/camera 流时不能误停）', async () => {
  const { stream, stopped } = fakeStream()
  global.fetch = async () => ({ ok: true, json: async () => ({ frame_id: 'vf_1' }) })
  await captureFrame('http://x', {
    getStream: async () => stream, grab: async () => new Uint8Array([1]),
  })
  assert.equal(stopped.length, 0, '注入流的生命周期归调用方')
})

test('任何异常都被吞掉（视觉是增强不是主干）', async () => {
  const id = await captureFrame('http://x', {
    getStream: async () => { throw new Error('permission denied') },
  })
  assert.equal(id, '')
})
