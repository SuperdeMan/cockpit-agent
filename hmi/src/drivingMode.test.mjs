import test from 'node:test'
import assert from 'node:assert/strict'
import { NO_EDGE_DRIVING, DRIVING_EXIT_GRACE_MS, drivingActive, projectDrivingFrame } from './drivingMode.mjs'

test('a simple final enters driving and consecutive false frames do not postpone the 30 s exit', () => {
  let edge = projectDrivingFrame(NO_EDGE_DRIVING, { type: 'final', driving: true }, 1000)
  assert.equal(drivingActive({ manual: false, edge, now: 1000 }), true)
  edge = projectDrivingFrame(edge, { type: 'process', driving: true }, 2000)
  assert.equal(edge.trueAt, 1000)
  edge = projectDrivingFrame(edge, { type: 'final', driving: false }, 3000)
  edge = projectDrivingFrame(edge, { type: 'final', driving: false }, 5000)
  assert.equal(edge.falseAt, 3000)
  assert.equal(drivingActive({ manual: false, edge, now: 3000 + DRIVING_EXIT_GRACE_MS - 1 }), true)
  assert.equal(drivingActive({ manual: false, edge, now: 3000 + DRIVING_EXIT_GRACE_MS }), false)
})

test('dismissal suppresses only the current segment; manual mode and a new segment can enter', () => {
  let edge = projectDrivingFrame(NO_EDGE_DRIVING, { type: 'process', driving: true }, 1000)
  edge = projectDrivingFrame(edge, { type: 'final', driving: true }, 4000)
  assert.equal(drivingActive({ manual: false, edge, now: 4000, dismissedAt: 2000 }), false)
  assert.equal(drivingActive({ manual: true, edge, now: 4000, dismissedAt: 2000 }), true)
  edge = projectDrivingFrame(edge, { type: 'final', driving: false }, 5000)
  edge = projectDrivingFrame(edge, { type: 'final', driving: true }, 6000)
  assert.equal(drivingActive({ manual: false, edge, now: 6000, dismissedAt: 2000 }), true)
})

test('missing, malformed or unrelated frames cannot invent a parked fact or mutate the frame', () => {
  const edge = { trueAt: 1000, falseAt: 0 }
  for (const frame of [null, {}, { type: 'final' }, { type: 'final', driving: 'false' },
    { type: 'vehicle_state', speed_kmh: 0, gear: 'P', driving: false }, { type: 'speech_delta', driving: false }]) {
    assert.equal(projectDrivingFrame(edge, frame && Object.freeze(frame), 2000), edge)
  }
})
