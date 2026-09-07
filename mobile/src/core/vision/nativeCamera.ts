import { requireOptionalNativeModule } from 'expo-modules-core'

interface MemoryCamera {
  supportsMemoryOnly?: boolean
  cameraActive?: boolean
  addListener(name: 'cameraActiveChanged', fn: (event: { active: boolean }) => void): { remove(): void }
  getMemoryCaptureStatsAsync(): Promise<Record<string, number | boolean>>
}

export function memoryCamera(): MemoryCamera | null {
  const module = requireOptionalNativeModule<MemoryCamera>('ExpoCamera')
  return module?.supportsMemoryOnly === true ? module : null
}
