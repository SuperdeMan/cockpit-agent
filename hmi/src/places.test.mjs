import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parsePlacesValue, isPlaceSet, formatPlace, PLACE_DEFS } from './places.mjs'

test('parsePlacesValue parses a JSON string into a places map', () => {
  const raw = JSON.stringify({ home: { name: '阳光小区', address: '上海长宁', lat: 1, lng: 2 } })
  const places = parsePlacesValue(raw)
  assert.equal(places.home.name, '阳光小区')
  assert.equal(places.home.address, '上海长宁')
})

test('parsePlacesValue accepts an already-parsed object', () => {
  assert.equal(parsePlacesValue({ company: { address: '深圳' } }).company.address, '深圳')
})

test('parsePlacesValue returns {} for missing / invalid / non-object values', () => {
  assert.deepEqual(parsePlacesValue(''), {})
  assert.deepEqual(parsePlacesValue(undefined), {})
  assert.deepEqual(parsePlacesValue('not json'), {})
  assert.deepEqual(parsePlacesValue('[1,2]'), {})
})

test('isPlaceSet treats a place with address or name as set', () => {
  assert.equal(isPlaceSet({ address: '上海' }), true)
  assert.equal(isPlaceSet({ name: '家' }), true)
  assert.equal(isPlaceSet({}), false)
  assert.equal(isPlaceSet(undefined), false)
})

test('formatPlace formats name and address together', () => {
  assert.equal(formatPlace({ name: '腾讯大厦', address: '海天二路33号' }), '腾讯大厦 · 海天二路33号')
  assert.equal(formatPlace({ address: '只有地址' }), '只有地址')
  assert.equal(formatPlace({}), '')
})

test('PLACE_DEFS covers home / company / school in display order', () => {
  assert.deepEqual(PLACE_DEFS.map((d) => d.key), ['home', 'company', 'school'])
  for (const d of PLACE_DEFS) {
    assert.ok(d.label)
    assert.ok(d.hint)
  }
})
