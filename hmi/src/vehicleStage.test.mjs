import test from 'node:test'
import assert from 'node:assert/strict'

import { stageMetrics } from './vehicleStage.mjs'

test('电量已知也不能推算没有读到的续航', () => {
  const [bat, range, gear] = stageMetrics({ battery: 72, gear: 'D', speed_kmh: 30 })
  assert.deepEqual(bat, { label: '电量', value: '72', unit: '%' })
  assert.deepEqual(range, { label: '续航', value: '读不到', unit: '' })
  assert.equal(gear.value, 'D')
})

test('range_km 信号存在时优先直用（不折算）', () => {
  const [, range] = stageMetrics({ battery: 50, range_km: 123, gear: 'P' })
  assert.equal(range.value, '123')
})

test('镜像未就绪/缺键：明确读不到，不给数值或单位', () => {
  for (const s of [undefined, null, {}, { location: null }]) {
    const [bat, range, gear] = stageMetrics(s)
    assert.equal(bat.value, '读不到')
    assert.equal(range.value, '读不到')
    assert.equal(gear.value, '读不到')
  }
})

test('字符串数值与小数电量容错', () => {
  const [bat, range] = stageMetrics({ battery: '61.5', gear: 'R' })
  assert.equal(bat.value, '62')                        // 四舍五入
  assert.equal(range.value, '读不到')
  const [bad] = stageMetrics({ battery: 'abc' })
  assert.equal(bad.value, '读不到')
})
