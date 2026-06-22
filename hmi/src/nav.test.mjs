import { test } from 'node:test'
import assert from 'node:assert/strict'
import { poiSelectionIndex } from './nav.mjs'

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
