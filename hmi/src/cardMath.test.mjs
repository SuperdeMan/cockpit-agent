import test from 'node:test'
import assert from 'node:assert/strict'

import { airQualityBadge, buildKlineGeometry, priceDirection } from './cardMath.mjs'

test('maps AQI values to the Chinese air-quality level used by the weather badge', () => {
  assert.deepEqual(airQualityBadge('28', '优'), { tone: 'excellent', label: '优' })
  assert.deepEqual(airQualityBadge('88', '良'), { tone: 'good', label: '良' })
  assert.deepEqual(airQualityBadge('128', ''), { tone: 'light', label: '轻度污染' })
  assert.deepEqual(airQualityBadge('175', '中度污染'), { tone: 'moderate', label: '中度污染' })
  assert.deepEqual(airQualityBadge('255', '重度污染'), { tone: 'heavy', label: '重度污染' })
  assert.deepEqual(airQualityBadge('320', '严重污染'), { tone: 'severe', label: '严重污染' })
})

test('keeps a provider category visible when AQI is unavailable', () => {
  assert.deepEqual(airQualityBadge('', '良'), { tone: 'good', label: '良' })
  assert.deepEqual(airQualityBadge('', ''), { tone: 'unknown', label: '暂无分级' })
})

test('uses Chinese market direction semantics: gains red and losses green', () => {
  assert.equal(priceDirection('+1.25'), 'up')
  assert.equal(priceDirection('-0.80'), 'down')
  assert.equal(priceDirection('0'), 'flat')
})

test('projects OHLC candles into bounded geometry with theme-addressable red-up green-down bodies', () => {
  const candles = buildKlineGeometry([
    { date: '2026-06-19', open: '100', high: '106', low: '98', close: '105' },
    { date: '2026-06-20', open: '105', high: '107', low: '99', close: '101' },
  ], 320, 150)

  assert.equal(candles.length, 2)
  assert.equal(candles[0].color, 'var(--stock-up)')
  assert.equal(candles[1].color, 'var(--stock-down)')
  assert.ok(candles[0].highY < candles[0].lowY)
  assert.ok(candles[0].bodyHeight >= 2)
})
