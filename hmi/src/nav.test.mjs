import { test } from 'node:test'
import assert from 'node:assert/strict'
import { poiSelectionIndex, isRefreshRequest } from './nav.mjs'

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

test('returns -1 for non-selection text (does not hijack normal queries)', () => {
  assert.equal(poiSelectionIndex('导航去厦门火车站'), -1)
  assert.equal(poiSelectionIndex('今天天气怎么样'), -1)
  assert.equal(poiSelectionIndex('第一个充电站怎么走'), -1) // 不是纯序号选择
  assert.equal(poiSelectionIndex(''), -1)
  assert.equal(poiSelectionIndex('厦门北站'), -1) // 名称选择走正常导航分发
})
