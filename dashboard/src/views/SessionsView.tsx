// Compatibility entry; the turn list and saved preset share one implementation.
import { TurnsView } from './TurnsView'
import type { Turn } from '../types'
export function SessionsView({ lastTurn }: { lastTurn: Turn | null }) { return <TurnsView lastTurn={lastTurn} /> }
