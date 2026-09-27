export interface VehicleProjection {
  vehicleId: string
  userId: string
  epoch: string
  revision: number
  state: Record<string, unknown>
  signals: Record<string, Record<string, unknown>>
  label: string
  legacy: boolean
}
export function emptyVehicleProjection(legacyVehicleId?: string): VehicleProjection
export function bindVehicleIdentity(current: VehicleProjection, frame: unknown): VehicleProjection
export function projectVehicleFrame(current: VehicleProjection, frame: unknown): VehicleProjection
