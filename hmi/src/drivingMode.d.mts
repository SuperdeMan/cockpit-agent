export interface DrivingEdgeFact { trueAt: number; falseAt: number }
export const DRIVING_EXIT_GRACE_MS: number
export const NO_EDGE_DRIVING: DrivingEdgeFact
export function drivingActive(input: { manual: boolean; edge: DrivingEdgeFact; now: number; dismissedAt?: number }): boolean
export function recordEdgeDriving(previous: DrivingEdgeFact, driving: boolean, now: number): DrivingEdgeFact
export function projectDrivingFrame(previous: DrivingEdgeFact, frame: unknown, now: number): DrivingEdgeFact
