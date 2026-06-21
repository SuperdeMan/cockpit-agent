import test from 'node:test'
import assert from 'node:assert/strict'

import { weatherAlertSummary } from './weatherCard.mjs'

test('turns the active weather warning into a compact card callout', () => {
  assert.deepEqual(weatherAlertSummary([
    { type: '暴雨', level: '蓝', title: '北京市气象台发布暴雨蓝色预警', text: '未来六小时有短时强降雨', pub_time: '2026-06-21T10:00+08:00' },
    { type: '雷电', level: '黄', title: '雷电黄色预警', text: '', pub_time: '' },
  ]), {
    headline: '暴雨蓝色预警',
    detail: '未来六小时有短时强降雨',
    extraCount: 1,
    publishedAt: '2026-06-21 10:00',
  })
})

test('returns no callout when there is no active warning', () => {
  assert.equal(weatherAlertSummary([]), null)
})
