// 单帧仅在内存存活。捕获、上传与请求共用取消边界，不读取任何 file URI。
export { needsFrame as needsVisionFrame, VISION_GUARD, VISION_TRIGGER } from '@shared/visionFrame.mjs'

import { checkCaptureSignal, withCaptureSignal } from './cancellation'
import { activityLog } from '../presence/activityLog'

type Capturer = (signal: AbortSignal) => Promise<Uint8Array | null>
let capturer: Capturer | null = null
let capability = new AbortController()
capability.abort()
export const visionCapabilitySignal = (): AbortSignal => capability.signal
let pending: AbortController | null = null
export const VISION_TIMEOUT_MS = 12_000

export interface VisionCaptureSnapshot {
  preparing: boolean
  cameraActive: boolean
  uploading: boolean
  uploadsStarted: number
  uploadsCompleted: number
}
let snapshot: VisionCaptureSnapshot = { preparing: false, cameraActive: false, uploading: false, uploadsStarted: 0, uploadsCompleted: 0 }
const subs = new Set<() => void>()
const capturingSubs = new Set<(value: boolean) => void>()
export const getVisionCaptureSnapshot = (): VisionCaptureSnapshot => snapshot
export function subscribeVisionCapture(fn: () => void): () => void {
  subs.add(fn)
  return () => { subs.delete(fn) }
}
function publish(patch: Partial<VisionCaptureSnapshot>): void {
  const before = isVisionCapturing()
  snapshot = { ...snapshot, ...patch }
  for (const fn of subs) fn()
  if (before !== isVisionCapturing()) for (const fn of capturingSubs) fn(isVisionCapturing())
}
/** Only the native CameraState observer reports this; unmount is not proof of CLOSED. */
export function reportVisionCameraActive(cameraActive: boolean): void {
  if (snapshot.cameraActive !== cameraActive) {
    if (cameraActive) activityLog.push('camera', '单帧相机已开启（设备采集）')
    publish({ cameraActive })
  }
}
export function cancelVisionCapture(): void { pending?.abort() }
export function registerVisionCapturer(fn: Capturer | null): void {
  if (capturer === fn) return
  capability.abort()
  cancelVisionCapture()
  capability = new AbortController()
  if (!fn) capability.abort()
  capturer = fn
}
export function visionCaptureReady(): boolean { return capturer !== null }
export function isVisionCapturing(): boolean { return snapshot.preparing || snapshot.cameraActive || snapshot.uploading }
export function subscribeVisionCapturing(fn: (value: boolean) => void): () => void {
  capturingSubs.add(fn)
  return () => { capturingSubs.delete(fn) }
}

/** Ordinary capture/network failure degrades honestly; revocation cancels the dependent request. */
export async function captureVisionFrame(audioUrl: string, requestSignal?: AbortSignal): Promise<string> {
  const fn = capturer
  if (requestSignal) checkCaptureSignal(requestSignal)
  if (!fn || !audioUrl || pending) return ''
  const ctl = new AbortController()
  pending = ctl
  const abort = () => ctl.abort()
  requestSignal?.addEventListener('abort', abort, { once: true })
  const timer = setTimeout(abort, VISION_TIMEOUT_MS)
  publish({ preparing: true })
  let bytes: Uint8Array | null = null
  try {
    bytes = await withCaptureSignal(fn(ctl.signal), ctl.signal)
    checkCaptureSignal(ctl.signal)
    if (!bytes?.length) return ''
    publish({ preparing: false, uploading: true, uploadsStarted: snapshot.uploadsStarted + 1 })
    const response = await withCaptureSignal(fetch(`${audioUrl}/api/vision/frame?mime=image/jpeg`, {
      method: 'POST',
      body: bytes as unknown as BodyInit,
      headers: { 'Content-Type': 'image/jpeg' },
      signal: ctl.signal,
    }), ctl.signal)
    checkCaptureSignal(ctl.signal)
    if (!response.ok) return ''
    const data = await withCaptureSignal(response.json(), ctl.signal) as { frame_id?: unknown }
    checkCaptureSignal(ctl.signal)
    publish({ uploadsCompleted: snapshot.uploadsCompleted + 1 })
    return typeof data.frame_id === 'string' ? data.frame_id : ''
  } catch (error) {
    checkCaptureSignal(ctl.signal)
    return ''
  } finally {
    bytes?.fill(0)
    clearTimeout(timer)
    requestSignal?.removeEventListener('abort', abort)
    if (pending === ctl) {
      pending = null
      publish({ preparing: false, uploading: false })
    }
  }
}
