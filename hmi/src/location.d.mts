export type BrowserLocation = { lat: number; lng: number; accuracyM?: number; capturedAt?: number }
export function buildLocationMeta(location?: BrowserLocation | null): Record<string, string>
export function buildRequestLocationMeta(enabled: boolean, location?: BrowserLocation | null): Record<string, string>
export function isLocationDependent(text: unknown): boolean
export function shouldRequestLocationConsent(text: unknown, locationEnabled: boolean): boolean
export function requestCurrentLocation(): Promise<Required<BrowserLocation>>
