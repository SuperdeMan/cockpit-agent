import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { AppState, type AppStateStatus } from 'react-native'

let mockPermission = { granted: true }
const mockRequestPermission = jest.fn()
const mockTakePicture = jest.fn()
const mockNativeListeners = new Set<(event: { active: boolean }) => void>()
let mockNativeCamera: { supportsMemoryOnly?: boolean; cameraActive?: boolean; addListener: jest.Mock } | null

jest.mock('expo-modules-core', () => ({ ...jest.requireActual('expo-modules-core'), requireOptionalNativeModule: () => mockNativeCamera }))
jest.mock('expo-camera', () => {
  const React = jest.requireActual('react')
  return {
    CameraView: React.forwardRef((props: object, ref: unknown) => {
      React.useImperativeHandle(ref, () => ({ takePictureAsync: (...args: unknown[]) => mockTakePicture(...args) }))
      return React.createElement('TestCamera', props)
    }),
    useCameraPermissions: () => [mockPermission, mockRequestPermission],
  }
})

import { VisionCapture } from '@/features/vision/VisionCapture'
import { DEFAULT_APP_SETTINGS, settingsStore } from '@/core/settings/store'
import {
  captureVisionFrame, getVisionCaptureSnapshot, registerVisionCapturer, reportVisionCameraActive, visionCaptureReady,
} from '@/core/vision/frame'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve() }
const originalFetch = global.fetch
const originalAppState = AppState.currentState
const appListeners = new Set<(state: AppStateStatus) => void>()
const views = new Set<ReactTestRenderer>()
async function mount(enabled = true) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(VisionCapture, { enabled })) })
  views.add(view)
  return view
}
async function unmount(view: ReactTestRenderer) {
  await act(async () => { view.unmount() })
  views.delete(view)
}
function cameras(view: ReactTestRenderer) {
  return view.root.findAll((node) => typeof node.props.onCameraReady === 'function')
}
async function start() {
  const controller = new AbortController()
  let outcome!: Promise<{ value?: string; error?: Error }>
  await act(async () => {
    outcome = captureVisionFrame('https://audio.test', controller.signal).then(
      (value) => ({ value }), (error: Error) => ({ error }),
    )
    await tick()
  })
  return { controller, outcome }
}
async function setEnabled(enabled: boolean) {
  await act(async () => {
    settingsStore.setState({ settings: { ...settingsStore.getState().settings, visionEnabled: enabled } })
    await tick()
  })
}
async function setAppState(state: AppStateStatus) {
  await act(async () => {
    AppState.currentState = state
    for (const listener of appListeners) listener(state)
    await tick()
  })
}
const picture = () => ({ base64: '/9j/2Q==', width: 1, height: 1 })

beforeEach(() => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  mockPermission = { granted: true }
  mockRequestPermission.mockResolvedValue({ granted: true })
  mockTakePicture.mockResolvedValue(picture())
  mockNativeCamera = {
    supportsMemoryOnly: true,
    cameraActive: false,
    addListener: jest.fn((_name, listener: (event: { active: boolean }) => void) => {
      mockNativeListeners.add(listener)
      return { remove: () => mockNativeListeners.delete(listener) }
    }),
  }
  AppState.currentState = 'active'
  jest.spyOn(AppState, 'addEventListener').mockImplementation((_name, listener) => {
    appListeners.add(listener as (state: AppStateStatus) => void)
    return { remove: () => appListeners.delete(listener as (state: AppStateStatus) => void) }
  })
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ frame_id: 'frame-test' }) })) as jest.Mock
  settingsStore.setState({ settings: { ...DEFAULT_APP_SETTINGS, visionEnabled: true } })
  registerVisionCapturer(null)
  reportVisionCameraActive(false)
})
afterEach(async () => {
  for (const view of views) await unmount(view)
  registerVisionCapturer(null)
  reportVisionCameraActive(false)
  mockNativeListeners.clear()
  appListeners.clear()
  global.fetch = originalFetch
  AppState.currentState = originalAppState
  settingsStore.setState({ settings: DEFAULT_APP_SETTINGS })
  jest.restoreAllMocks()
  jest.useRealTimers()
})

test.each([false, undefined, null])('R02: missing memory-only native capability %s never requests permission or mounts a camera', async (support) => {
  if (support === null) mockNativeCamera = null
  else mockNativeCamera!.supportsMemoryOnly = support
  const view = await mount()
  expect(visionCaptureReady()).toBe(false)
  await expect((await start()).outcome).resolves.toEqual({ value: '' })
  expect(cameras(view)).toHaveLength(0)
  expect(mockRequestPermission).not.toHaveBeenCalled()
  expect(mockTakePicture).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})

test.each(['disable', 'background', 'unmount'])('R02: %s during a permission prompt prevents camera creation even after permission is granted', async (reason) => {
  mockPermission = { granted: false }
  const permission = deferred<{ granted: boolean }>()
  mockRequestPermission.mockReturnValue(permission.promise)
  const view = await mount()
  const operation = await start()
  expect(mockRequestPermission).toHaveBeenCalledTimes(1)
  expect(cameras(view)).toHaveLength(0)
  if (reason === 'disable') await setEnabled(false)
  else if (reason === 'background') await setAppState('background')
  else await unmount(view)
  expect((await operation.outcome).error?.name).toBe('AbortError')
  await act(async () => { permission.resolve({ granted: true }); await tick() })
  if (reason !== 'unmount') expect(cameras(view)).toHaveLength(0)
  expect(mockTakePicture).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})

test('R02: disabling and re-enabling does not revive the old permission callback; a new request can recover', async () => {
  mockPermission = { granted: false }
  const permission = deferred<{ granted: boolean }>()
  mockRequestPermission.mockReturnValue(permission.promise)
  const view = await mount()
  const first = await start()
  await setEnabled(false)
  await setEnabled(true)
  expect((await first.outcome).error?.name).toBe('AbortError')
  await act(async () => { permission.resolve({ granted: true }); await tick() })
  expect(cameras(view)).toHaveLength(0)
  expect(mockTakePicture).not.toHaveBeenCalled()
  mockPermission = { granted: true }
  await act(async () => { view.update(createElement(VisionCapture, { enabled: true })) })
  const second = await start()
  expect(cameras(view).length).toBeGreaterThan(0)
  await act(async () => { cameras(view)[0].props.onCameraReady(); await tick() })
  expect(await second.outcome).toEqual({ value: 'frame-test' })
  expect(mockTakePicture).toHaveBeenCalledTimes(1)
  expect(mockTakePicture).toHaveBeenCalledWith({ memoryOnly: true, quality: 0.7, shutterSound: false })
})

test.each(['onCameraReady', 'onMountError'])('R02: stale %s from an unmounted camera cannot settle the next capture', async (event) => {
  const view = await mount()
  const first = await start()
  const stale = cameras(view)[0].props[event] as () => void
  await act(async () => { first.controller.abort(); await tick() })
  expect((await first.outcome).error?.name).toBe('AbortError')
  const second = await start()
  let settled = false
  void second.outcome.then(() => { settled = true })
  await act(async () => { stale(); await tick() })
  expect(mockTakePicture).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
  expect(settled).toBe(false)
  await act(async () => { cameras(view)[0].props.onCameraReady(); await tick() })
  expect(await second.outcome).toEqual({ value: 'frame-test' })
})

test.each(['disable', 'background', 'unmount'])('R02: %s while taking the picture rejects the late image without uploading it', async (reason) => {
  const image = deferred<ReturnType<typeof picture>>()
  mockTakePicture.mockReturnValue(image.promise)
  const view = await mount()
  const operation = await start()
  await act(async () => { cameras(view)[0].props.onCameraReady(); await tick() })
  expect(mockTakePicture).toHaveBeenCalledTimes(1)
  if (reason === 'disable') await setEnabled(false)
  else if (reason === 'background') await setAppState('background')
  else await unmount(view)
  expect((await operation.outcome).error?.name).toBe('AbortError')
  await act(async () => { image.resolve(picture()); await tick() })
  if (reason !== 'unmount') expect(cameras(view)).toHaveLength(0)
  expect(fetch).not.toHaveBeenCalled()
})

test('R02: a native response containing a file URI is rejected despite a claimed memory capability', async () => {
  mockTakePicture.mockResolvedValue({ ...picture(), uri: 'file:///cache/should-not-exist.jpg' })
  const view = await mount()
  const operation = await start()
  await act(async () => { cameras(view)[0].props.onCameraReady(); await tick() })
  expect(await operation.outcome).toEqual({ value: '' })
  expect(fetch).not.toHaveBeenCalled()
})

test('R02: capability shutdown requests unmount but only native CLOSED clears the physical camera fact', async () => {
  await mount()
  const operation = await start()
  await act(async () => { for (const listener of mockNativeListeners) listener({ active: true }) })
  expect(getVisionCaptureSnapshot().cameraActive).toBe(true)
  await setEnabled(false)
  expect((await operation.outcome).error?.name).toBe('AbortError')
  expect(getVisionCaptureSnapshot().cameraActive).toBe(true)
  await act(async () => { for (const listener of mockNativeListeners) listener({ active: false }) })
  expect(getVisionCaptureSnapshot().cameraActive).toBe(false)
})
