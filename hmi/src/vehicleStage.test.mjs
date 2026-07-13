import test from 'node:test'
import assert from 'node:assert/strict'

import { stageMetrics, RANGE_FULL_KM } from './vehicleStage.mjs'

test('镜像就绪：电量/续航/挡位动态取数（续航=电量折算）', () => {
  const [bat, range, gear] = stageMetrics({ battery: 72, gear: 'D', speed_kmh: 30 })
  assert.deepEqual(bat, { label: '电量', value: '72', unit: '%' })
  assert.equal(range.value, String(Math.round((72 / 100) * RANGE_FULL_KM)))
  assert.equal(gear.value, 'D')
})

test('range_km 信号存在时优先直用（不折算）', () => {
  const [, range] = stageMetrics({ battery: 50, range_km: 123, gear: 'P' })
  assert.equal(range.value, '123')
})

test('镜像未就绪/缺键：诚实占位 --（不再假装 62%/430km/P）', () => {
  for (const s of [undefined, null, {}, { location: null }]) {
    const [bat, range, gear] = stageMetrics(s)
    assert.equal(bat.value, '--')
    assert.equal(range.value, '--')
    assert.equal(gear.value, '--')
  }
})

test('字符串数值与小数电量容错', () => {
  const [bat, range] = stageMetrics({ battery: '61.5', gear: 'R' })
  assert.equal(bat.value, '62')                        // 四舍五入
  assert.equal(range.value, String(Math.round(0.615 * RANGE_FULL_KM)))
  const [bad] = stageMetrics({ battery: 'abc' })
  assert.equal(bad.value, '--')
})
