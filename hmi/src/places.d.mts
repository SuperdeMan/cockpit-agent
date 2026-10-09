export type PlaceLabel = { name?: string; address?: string; lat?: number; lng?: number }
export const PLACE_DEFS: Array<{ key: string; label: string; icon: string; hint: string }>
export function parsePlacesValue(raw: unknown): Record<string, PlaceLabel>
export function isPlaceSet(place: PlaceLabel | null | undefined): boolean
export function formatPlace(place: PlaceLabel | null | undefined): string
