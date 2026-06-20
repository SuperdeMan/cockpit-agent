import test from 'node:test'
import assert from 'node:assert/strict'

import { buildKlineGeometry, priceDirection } from './cardMath.mjs'

test('uses Chinese market direction semantics: gains red and losses green', () => {
  assert.equal(priceDirection('+1.25'), 'up')
  assert.equal(priceDirection('-0.80'), 'down')
  assert.equal(priceDirection('0'), 'flat')
})

test('projects OHLC candles into bounded geometry with red-up green-down bodies', () => {
  const candles = buildKlineGeometry([
    { date: '2026-06-19', open: '100', high: '106', low: '98', close: '105' },
    { date: '2026-06-20', open: '105', high: '107', low: '99', close: '101' },
  ], 320, 150)

  assert.equal(candles.length, 2)
  assert.equal(candles[0].color, '#ff5b55')
  assert.equal(candles[1].color, '#2fb37b')
  assert.ok(candles[0].highY < candles[0].lowY)
  assert.ok(candles[0].bodyHeight >= 2)
})
