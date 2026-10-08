// Shared presentation policy, extracted unchanged from mobile/core/presence/drivingMode.ts.
// The server decides driving; this module never infers it from speed or gear.
export const DRIVING_EXIT_GRACE_MS = 30_000
export const NO_EDGE_DRIVING = { trueAt: 0, falseAt: 0 }

export function drivingActive({ manual, edge, now, dismissedAt = 0 }) {
  if (manual) return true
  if (edge.trueAt <= 0 || dismissedAt >= edge.trueAt) return false
  if (edge.falseAt <= edge.trueAt) return true
  return now - edge.falseAt < DRIVING_EXIT_GRACE_MS
}

export function recordEdgeDriving(prev, driving, now) {
  if (driving) {
    const inSegment = prev.trueAt > 0 && prev.falseAt <= prev.trueAt
    return inSegment ? prev : { trueAt: now, falseAt: 0 }
  }
  if (prev.trueAt <= 0 || prev.falseAt > prev.trueAt) return prev
  return { trueAt: prev.trueAt, falseAt: now }
}

// Only explicit server facts enter the HMI visual projection. Missing is not false.
export function projectDrivingFrame(previous, frame, now) {
  if (!['process', 'final'].includes(frame?.type) || typeof frame.driving !== 'boolean') return previous
  return recordEdgeDriving(previous, frame.driving, now)
}
