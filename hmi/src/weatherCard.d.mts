import type { WeatherCard } from './types'
type Alerts = WeatherCard['alerts']
export function weatherAlertSummary(alerts: Alerts): { headline: string; detail: string; extraCount: number; publishedAt: string } | null
export function weatherAlertStatus(alerts: Alerts, available?: boolean): { tone: 'unavailable' | 'warning' | 'clear'; label: string }
