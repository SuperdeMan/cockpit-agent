export class PcmRing {
  constructor(capacityMs?: number)
  push(frame: Float32Array | null | undefined): void
  takeLast(ms: number): Float32Array<ArrayBuffer>
  clear(): void
  readonly frames: number
}
export function float32ToInt16(samples: Float32Array): Int16Array<ArrayBuffer>
export function int16ToFloat32(samples: Int16Array): Float32Array<ArrayBuffer>
export function int16ToWav(samples: Int16Array, sampleRate?: number): Uint8Array<ArrayBuffer>
