import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildLocationMeta,
  buildRequestLocationMeta,
  shouldRequestLocationConsent,
  isLocationDependent,
} from './location.mjs'

test('isLocationDependent flags navigation / nearest / where-am-i / unscoped weather', () => {
  assert.equal(isLocationDependent('导航去最近的粤菜馆'), true)
  assert.equal(isLocationDependent('附近的充电站'), true)
  assert.equal(isLocationDependent('我现在在哪里'), true)
  assert.equal(isLocationDependent('今天天气怎么样'), true)
  // 已含明确城市的天气不需要当前定位；纯闲聊也不需要
  assert.equal(isLocationDependent('深圳天气怎么样'), false)
  assert.equal(isLocationDependent('讲个笑话'), false)
})

test('serializes one browser-approved position into request-only location meta', () => {
  assert.deepEqual(buildLocationMeta({ lat: 39.92, lng: 116.41, accuracyM: 12, capturedAt: 123 }), {
    current_lat: '39.920000',
    current_lng: '116.410000',
    current_accuracy_m: '12',
    current_location_at: '123',
    current_location_source: 'browser',
  })
})

test('does not send invalid coordinates', () => {
  assert.deepEqual(buildLocationMeta({ lat: 91, lng: 116.41 }), {})
})

test('does not attach a previously captured location after the setting is disabled', () => {
  const location = { lat: 39.92, lng: 116.41, accuracyM: 12, capturedAt: 1_781_700_000_000 }
  assert.deepEqual(buildRequestLocationMeta(false, location), {})
  assert.equal(buildRequestLocationMeta(true, location).current_lat, '39.920000')
})

test('asks for consent for weather without a named place and for navigation origin', () => {
  assert.equal(shouldRequestLocationConsent('今天天气怎么样', false), true)
  assert.equal(shouldRequestLocationConsent('我这里天气怎么样', false), true)
  assert.equal(shouldRequestLocationConsent('导航去东方明珠', false), true)
  assert.equal(shouldRequestLocationConsent('深圳天气怎么样', false), false)
  assert.equal(shouldRequestLocationConsent('今天天气怎么样', true), false)
})

test('isLocationDependent flags charging / trip-planning queries', () => {
  assert.equal(isLocationDependent('是否需要中途充电'), true)
  assert.equal(isLocationDependent('周末去杭州两天，带老人，顺便看看是否需要中途充电'), true)
  assert.equal(isLocationDependent('帮我规划行程'), true)
})
