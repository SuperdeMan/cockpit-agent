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

test('keeps weather-alert status visible even when there is no active warning', async () => {
  const { weatherAlertStatus } = await import('./weatherCard.mjs')
  assert.deepEqual(weatherAlertStatus([]), { tone: 'clear', label: '暂无天气预警' })
  assert.deepEqual(weatherAlertStatus([
    { type: '暴雨', level: '蓝', title: '暴雨蓝色预警' },
  ]), { tone: 'warning', label: '暴雨蓝色预警' })
})

test('does not label denied weather warnings as no warning', async () => {
  const { weatherAlertStatus } = await import('./weatherCard.mjs')
  assert.deepEqual(weatherAlertStatus([], false), { tone: 'unavailable', label: '预警服务暂不可用' })
})
