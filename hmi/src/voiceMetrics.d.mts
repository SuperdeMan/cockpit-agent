export interface MetricStorage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}
export function bumpVoiceMetric(event: string, storage?: MetricStorage): void
export function readCounts(storage?: MetricStorage): unknown
export function resetVoiceMetrics(storage?: MetricStorage): void
