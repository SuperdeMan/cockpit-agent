// View state only. No dispatch, vehicle control, confirmation or persistence.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { DRIVING_EXIT_GRACE_MS, NO_EDGE_DRIVING, drivingActive, projectDrivingFrame } from './drivingMode.mjs'

export function useDrivingProjection() {
  const [edge, setEdge] = useState(NO_EDGE_DRIVING)
  const [manual, setManual] = useState(false)
  const [dismissedAt, setDismissedAt] = useState(0)
  const [now, setNow] = useState(Date.now)
  const observe = useCallback((frame: unknown) => {
    const fact = frame as { type?: string; driving?: unknown } | null
    if (!fact || !['process', 'final'].includes(fact.type ?? '') || typeof fact.driving !== 'boolean') return
    const time = Date.now()
    setEdge(previous => projectDrivingFrame(previous, frame, time))
    setNow(time)
  }, [])
  const driving = drivingActive({ manual, edge, dismissedAt, now })
  useEffect(() => {
    if (edge.falseAt <= edge.trueAt) return
    const remaining = edge.falseAt + DRIVING_EXIT_GRACE_MS - Date.now()
    if (remaining <= 0) { setNow(Date.now()); return }
    const timer = window.setTimeout(() => setNow(Date.now()), remaining)
    return () => window.clearTimeout(timer)
  }, [edge])
  useEffect(() => {
    document.documentElement.dataset.drive = driving ? 'on' : 'off'
    return () => { document.documentElement.dataset.drive = 'off' }
  }, [driving])
  const setDriving = useCallback((enabled: boolean) => {
    const time = Date.now()
    setManual(enabled)
    if (!enabled) setDismissedAt(time)
    setNow(time)
  }, [])
  return useMemo(() => ({ driving, manual, observe, setDriving }), [driving, manual, observe, setDriving])
}

type DrivingView = ReturnType<typeof useDrivingProjection>
const DrivingContext = createContext<DrivingView | null>(null)
export function DrivingProvider({ value, children }: { value: DrivingView; children: ReactNode }) {
  return <DrivingContext.Provider value={value}>{children}</DrivingContext.Provider>
}
export function useDriving() {
  const value = useContext(DrivingContext)
  if (!value) throw new Error('useDriving must be inside DrivingProvider')
  return value
}
