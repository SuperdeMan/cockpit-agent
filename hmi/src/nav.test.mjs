import { test } from 'node:test'
import assert from 'node:assert/strict'
import { poiSelectionIndex, ordinalIn, ordinalSelectIn, isRefreshRequest } from './nav.mjs'

test('isRefreshRequest flags 换一批 / 换一个 / 还有别的, not normal queries', () => {
  for (const t of ['换一批', '换一个', '换一换', '下一批', '还有别的吗', '都不满意']) {
    assert.equal(isRefreshRequest(t), true, t)
  }
  for (const t of ['导航去最近的粤菜馆', '第二个', '今天天气怎么样']) {
    assert.equal(isRefreshRequest(t), false, t)
  }
})

test('parses Chinese ordinals to 0-based index', () => {
  assert.equal(poiSelectionIndex('第一个'), 0)
  assert.equal(poiSelectionIndex('第二个'), 1)
  assert.equal(poiSelectionIndex('第三'), 2)
  assert.equal(poiSelectionIndex('去第二个'), 1)
  assert.equal(poiSelectionIndex('导航去第三个'), 2)
})

test('parses digit ordinals', () => {
  assert.equal(poiSelectionIndex('第1个'), 0)
  assert.equal(poiSelectionIndex('2'), 1)
  assert.equal(poiSelectionIndex('第3'), 2)
})

test('ordinalIn extracts 第N个 from within a longer phrase (看第八个详情)', () => {
  assert.equal(ordinalIn('看看第八个的详情'), 7)   // 之前 poiSelectionIndex 整句锚定 → -1 → 退化第一个
  assert.equal(ordinalIn('第2个怎么样'), 1)
  assert.equal(ordinalIn('看第一个详情'), 0)
  assert.equal(ordinalIn('第十个的电话'), 9)
  assert.equal(ordinalIn('导航去厦门'), -1)          // 无序号
})

test('ordinalSelectIn catches bare/verb ordinal selections (点一下第九个), needs 个/家', () => {
  assert.equal(ordinalSelectIn('点一下第九个'), 8)   // 之前无「详情」线索词 → 漏接 → 后端乱返回
  assert.equal(ordinalSelectIn('第九个'), 8)
  assert.equal(ordinalSelectIn('看第八个的详情'), 7)
  assert.equal(ordinalSelectIn('导航去第2个'), 1)     // 命中；导航/详情由调用侧按导航词区分
  assert.equal(ordinalSelectIn('第一次来'), -1)       // 「第一」后非「个/家」→ 不误判为选择
  assert.equal(ordinalSelectIn('今天天气怎么样'), -1)
})

test('returns -1 for non-selection text (does not hijack normal queries)', () => {
  assert.equal(poiSelectionIndex('导航去厦门火车站'), -1)
  assert.equal(poiSelectionIndex('今天天气怎么样'), -1)
  assert.equal(poiSelectionIndex('第一个充电站怎么走'), -1) // 不是纯序号选择
  assert.equal(poiSelectionIndex(''), -1)
  assert.equal(poiSelectionIndex('厦门北站'), -1) // 名称选择走正常导航分发
})
