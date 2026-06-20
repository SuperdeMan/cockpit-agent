export type PriceDirection = 'up' | 'down' | 'flat'

export type KlineInput = {
  date: string
  open: string
  high: string
  low: string
  close: string
  volume?: string
}

export type KlineGeometry = KlineInput & {
  x: number
  highY: number
  lowY: number
  bodyY: number
  bodyHeight: number
  bodyWidth: number
  direction: PriceDirection
  color: string
}

export function priceDirection(change: string | number | undefined): PriceDirection
export function buildKlineGeometry(candles: KlineInput[], width?: number, height?: number): KlineGeometry[]
