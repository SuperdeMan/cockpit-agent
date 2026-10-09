// Saved cases are a time-unbounded preset of the turn list.
import { TurnsView } from './TurnsView'
export function BadcasesView({ turnTick = 0 }: { turnTick?: number }) { return <TurnsView key={turnTick} lastTurn={null} saved /> }
