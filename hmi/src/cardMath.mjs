const UP_COLOR = 'var(--stock-up)'
const DOWN_COLOR = 'var(--stock-down)'
const FLAT_COLOR = '#94a3b8'

function toNumber(value) {
  const text = String(value ?? '').trim()
  if (!text) return NaN
  const number = Number(text.replace(/[% ,]/g, ''))
  return Number.isFinite(number) ? number : NaN
}

const AIR_QUALITY_LEVELS = [
  { max: 50, tone: 'excellent', label: '优' },
  { max: 100, tone: 'good', label: '良' },
  { max: 150, tone: 'light', label: '轻度污染' },
  { max: 200, tone: 'moderate', label: '中度污染' },
  { max: 300, tone: 'heavy', label: '重度污染' },
  { max: Infinity, tone: 'severe', label: '严重污染' },
]

const AIR_QUALITY_CATEGORY_LEVELS = [
  { pattern: /严重/, tone: 'severe' },
  { pattern: /重度/, tone: 'heavy' },
  { pattern: /中度/, tone: 'moderate' },
  { pattern: /轻度/, tone: 'light' },
  { pattern: /良/, tone: 'good' },
  { pattern: /优/, tone: 'excellent' },
]

export function airQualityBadge(aqi, category = '') {
  const value = toNumber(aqi)
  if (Number.isFinite(value)) {
    const level = AIR_QUALITY_LEVELS.find(({ max }) => value <= max)
    return { tone: level.tone, label: String(category).trim() || level.label }
  }

  const label = String(category).trim()
  const level = AIR_QUALITY_CATEGORY_LEVELS.find(({ pattern }) => pattern.test(label))
  return { tone: level?.tone || 'unknown', label: label || '暂无分级' }
}

export function priceDirection(change) {
  const value = toNumber(change)
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}

export function buildKlineGeometry(rawCandles, width = 320, height = 150) {
  const candles = rawCandles
    .map((candle) => ({
      ...candle,
      openValue: toNumber(candle.open),
      highValue: toNumber(candle.high),
      lowValue: toNumber(candle.low),
      closeValue: toNumber(candle.close),
    }))
    .filter((candle) => [candle.openValue, candle.highValue, candle.lowValue, candle.closeValue]
      .every(Number.isFinite))

  if (!candles.length) return []

  const rawLow = Math.min(...candles.map((candle) => candle.lowValue))
  const rawHigh = Math.max(...candles.map((candle) => candle.highValue))
  const padding = Math.max((rawHigh - rawLow) * 0.08, rawHigh * 0.002, 0.01)
  const rangeLow = rawLow - padding
  const rangeHigh = rawHigh + padding
  const range = Math.max(rangeHigh - rangeLow, 0.01)
  const insetX = 16
  const insetY = 12
  const plotHeight = Math.max(height - insetY * 2, 1)
  const step = Math.max((width - insetX * 2) / candles.length, 1)
  const bodyWidth = Math.max(Math.min(step * 0.58, 14), 2)
  const y = (value) => insetY + ((rangeHigh - value) / range) * plotHeight

  return candles.map((candle, index) => {
    const openY = y(candle.openValue)
    const closeY = y(candle.closeValue)
    const highY = y(candle.highValue)
    const lowY = y(candle.lowValue)
    const direction = candle.closeValue > candle.openValue ? 'up'
      : candle.closeValue < candle.openValue ? 'down' : 'flat'
    const color = direction === 'up' ? UP_COLOR : direction === 'down' ? DOWN_COLOR : FLAT_COLOR
    return {
      ...candle,
      x: insetX + step * index + step / 2,
      highY,
      lowY,
      bodyY: Math.min(openY, closeY),
      bodyHeight: Math.max(Math.abs(openY - closeY), 2),
      bodyWidth,
      direction,
      color,
    }
  })
}
