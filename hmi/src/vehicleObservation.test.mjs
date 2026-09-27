import { test } from 'node:test'
import assert from 'node:assert/strict'
import { emptyVehicleProjection, bindVehicleIdentity, projectVehicleFrame } from './vehicleObservation.mjs'

const bound = (vehicle = 'v1', epoch = 'gateway-1') => bindVehicleIdentity(emptyVehicleProjection(),
  { type: 'session_identity', user_id: 'user-1', vehicle_id: vehicle, vehicle_state_epoch: epoch })
const frame = (vehicle = 'v1', revision = 1, quality = 'good') => ({
  type: 'vehicle_state', version: 2, projection_epoch: 'gateway-1', revision, vehicle_id: vehicle,
  state: { battery: 72 }, observation: { version: 2, vehicle_id: vehicle, state: { battery: 72 },
    signals: { battery: { quality, source_kind: 'simulated', authenticated: true } } },
})

test('a valid signed-source projection is still labelled simulated', () => {
  const p = projectVehicleFrame(bound(), frame())
  assert.equal(p.state.battery, 72)
  assert.equal(p.label, '模拟车况')
})
test('another vehicle cannot overwrite the selected vehicle', () => {
  const p = projectVehicleFrame(bound(), frame())
  assert.equal(projectVehicleFrame(p, frame('v2', 2)), p)
})
test('expired values are removed even if a defective frame still includes them', () => {
  const p = projectVehicleFrame(bound(), frame())
  const stale = projectVehicleFrame(p, frame('v1', 2, 'stale'))
  assert.deepEqual(stale.state, {})
  assert.match(stale.label, /待更新/)
})
test('an older projection and a previous gateway epoch cannot overwrite newer state', () => {
  const p = projectVehicleFrame(bound(), frame('v1', 3))
  assert.equal(projectVehicleFrame(p, frame('v1', 2)), p)
  assert.equal(projectVehicleFrame(bound('v1', 'gateway-2'), frame('v1', 4)).revision, 0)
})
test('a new connection identity clears previous vehicle values', () => {
  const p = projectVehicleFrame(bound(), frame())
  const changed = bindVehicleIdentity(p, { type: 'session_identity', user_id: 'user-1', vehicle_id: 'v2', vehicle_state_epoch: 'gateway-1' })
  assert.deepEqual(changed.state, {})
  assert.equal(changed.revision, 0)
})
test('legacy compatibility is v1-only and never downgrades an upgraded connection', () => {
  const old = { type: 'vehicle_state', state: { battery: 12 } }
  assert.equal(projectVehicleFrame(emptyVehicleProjection(), old).state.battery, 12)
  assert.deepEqual(projectVehicleFrame(emptyVehicleProjection('v2'), old).state, {})
  assert.equal(projectVehicleFrame(bound(), old).revision, 0)
})
test('a v2 frame needs the authenticated connection identity first', () => {
  const p = emptyVehicleProjection()
  assert.equal(projectVehicleFrame(p, frame()), p)
})
test('missing signal provenance cannot become a displayed reading', () => {
  const f = frame(); f.observation.signals = {}
  assert.deepEqual(projectVehicleFrame(bound(), f).state, {})
})
