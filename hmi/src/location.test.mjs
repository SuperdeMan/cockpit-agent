import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildLocationMeta,
  buildRequestLocationMeta,
  shouldRequestLocationConsent,
} from './location.mjs'

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
