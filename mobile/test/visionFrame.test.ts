import { captureVisionFrame, getVisionCaptureSnapshot, registerVisionCapturer, reportVisionCameraActive, VISION_TIMEOUT_MS } from '@/core/vision/frame'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }
const originalFetch = global.fetch
beforeEach(() => { jest.useFakeTimers(); global.fetch = jest.fn() })
afterEach(() => { registerVisionCapturer(null); reportVisionCameraActive(false); global.fetch = originalFetch; jest.useRealTimers() })

test('R02 uploads only in-memory JPEG bytes and clears the owned buffer after success', async () => {
  const bytes = new Uint8Array([255, 216, 255, 217])
  registerVisionCapturer(async () => bytes)
  ;(fetch as jest.Mock).mockImplementation(async (_url, init) => {
    expect([...init.body]).toEqual([255, 216, 255, 217])
    expect(init.signal.aborted).toBe(false)
    return { ok: true, json: async () => ({ frame_id: 'frame-1' }) }
  })
  expect(await captureVisionFrame('https://audio')).toBe('frame-1')
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch).toHaveBeenCalledWith('https://audio/api/vision/frame?mime=image/jpeg', expect.any(Object))
  expect([...bytes]).toEqual([0, 0, 0, 0])
  expect(getVisionCaptureSnapshot()).toMatchObject({ preparing: false, uploading: false })
})

test.each(['cancel', 'disable', 'timeout'])('R02 %s while camera/permission is pending rejects before upload; next round recovers', async (reason) => {
  const wait = deferred<Uint8Array | null>()
  const signal = new AbortController()
  registerVisionCapturer(() => wait.promise)
  const operation = captureVisionFrame('https://audio', signal.signal)
  const assertion = expect(operation).rejects.toHaveProperty('name', 'AbortError')
  if (reason === 'cancel') signal.abort()
  else if (reason === 'disable') registerVisionCapturer(null)
  else jest.advanceTimersByTime(VISION_TIMEOUT_MS)
  await assertion
  wait.resolve(new Uint8Array([1, 2]))
  await tick()
  expect(fetch).not.toHaveBeenCalled()
  registerVisionCapturer(async () => new Uint8Array([1]))
  ;(fetch as jest.Mock).mockResolvedValue({ ok: true, json: async () => ({ frame_id: 'next' }) })
  expect(await captureVisionFrame('https://audio')).toBe('next')
})

test('R02 revoke during upload aborts fetch and rejects late frame id; camera CLOSED is independent', async () => {
  const response = deferred<Response>()
  registerVisionCapturer(async () => new Uint8Array([1]))
  ;(fetch as jest.Mock).mockReturnValue(response.promise)
  const operation = captureVisionFrame('https://audio')
  const assertion = expect(operation).rejects.toHaveProperty('name', 'AbortError')
  await tick()
  reportVisionCameraActive(true)
  registerVisionCapturer(null)
  await assertion
  expect((fetch as jest.Mock).mock.calls[0][1].signal.aborted).toBe(true)
  response.resolve({ ok: true, json: async () => ({ frame_id: 'late' }) } as Response)
  await tick()
  expect(getVisionCaptureSnapshot()).toMatchObject({ cameraActive: true, uploading: false })
  reportVisionCameraActive(false)
  expect(getVisionCaptureSnapshot().cameraActive).toBe(false)
})

test.each(['capture', 'upload'])('R02 ordinary %s failure degrades without file reads; concurrent request cannot open a second camera', async (stage) => {
  const wait = deferred<Uint8Array | null>()
  const capture = jest.fn(() => wait.promise)
  registerVisionCapturer(capture)
  ;(fetch as jest.Mock).mockRejectedValue(new Error('offline'))
  const first = captureVisionFrame('https://audio')
  expect(await captureVisionFrame('https://audio')).toBe('')
  expect(capture).toHaveBeenCalledTimes(1)
  wait.resolve(stage === 'capture' ? null : new Uint8Array([1]))
  expect(await first).toBe('')
  expect(getVisionCaptureSnapshot()).toMatchObject({ preparing: false, uploading: false })
})
