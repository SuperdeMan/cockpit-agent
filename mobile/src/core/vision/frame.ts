// 视觉单帧抓取（实施计划 M4-6）。红线三条件（CLAUDE.md §5「视觉单帧同款三条件」）：
//   ① 设置默认关（`visionEnabled` 默认 false，见 settings/store.ts）
//   ② **端侧命中视觉触发词才抓一帧**，未命中一帧都不采
//   ③ 文案说清（设置页 + 卡片「模拟」角标）
// 图像只在网关内存里活 TTL 秒，**不落盘、不落 Redis、不进 obs、不进记忆**。
//
// **触发判据必须共用 `@shared/visionFrame.mjs::needsFrame`**：采集面就是隐私面，
// 判据分叉的那一刻，同一句话在 HMI 上不采、在手机上采（或反过来），而没有任何东西会红。
// 那个文件里的 `captureFrame` 是 DOM 实现（video+canvas），**App 侧禁引**——
// 白名单台账里为它写了具名例外条款，守卫测试逐条断言（同 location.mjs 的先例）。
//
// 条件②在 RN 上比在浏览器上更要小心：**挂载 CameraView 本身就是打开摄像头**。
// 所以这里不做「常驻一个隐藏预览、要用时拍一张」——那等于一直开着。
// 真实形态是：命中触发词 → 才挂载 → 等 onCameraReady → 拍一张 → **立刻卸载**。
// 这条由 features/vision/VisionCapture.tsx 落实，本文件只持有它注册进来的能力。
export { needsFrame as needsVisionFrame, VISION_GUARD, VISION_TRIGGER } from '@shared/visionFrame.mjs'

/** 由 VisionCapture 组件注册：拍一张，返回本地文件 uri（失败返回空串） */
type Capturer = () => Promise<string>

let capturer: Capturer | null = null

/** VisionCapture 挂载时注册、卸载时注销。没注册 = 这一档能力不存在（干净降级）。 */
export function registerVisionCapturer(fn: Capturer | null): void {
  capturer = fn
}

export function visionCaptureReady(): boolean {
  return capturer != null
}

/**
 * 抓一帧并上传，返回 frame_id；**任何失败返回空串**——由 Agent 侧诚实说「没拿到画面」，
 * 不在这里弹错误打断对话（与 hmi/src/visionFrame.mjs 同一处置）。
 */
export async function captureVisionFrame(audioUrl: string): Promise<string> {
  const fn = capturer
  if (!fn || !audioUrl) return ''
  try {
    const uri = await fn()
    if (!uri) return ''
    // RN 的 fetch 支持把 file:// uri 当 body 直传（不必先读进内存再转 base64）
    const blob = await (await fetch(uri)).blob()
    const r = await fetch(`${audioUrl}/api/vision/frame?mime=image/jpeg`, {
      method: 'POST',
      body: blob,
      headers: { 'Content-Type': 'image/jpeg' },
    })
    if (!r.ok) return ''
    const j = (await r.json()) as { frame_id?: string }
    return j.frame_id || ''
  } catch {
    return ''
  }
}
