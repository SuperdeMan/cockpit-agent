// 视觉单帧抓取（M4 P4）：命中触发词才抓，默认一帧都不采。
//
// **隐私门控必须在采集侧**（RFC §5.2）：不做「后端判断要图 → 回头让 HMI 补拍」——
// ① 多一个 RTT，而「那是什么」问的是**当下**，半秒后画面可能已经不是同一个东西；
// ② 门控放后端等于「先采了再说」，与「敏感数据默认不出车」的方向相反。
//
// 抓到的帧立刻上传换一个短 TTL 的 frame_id，本地不留、不进任何缓存。

/** 端侧视觉触发词。刻意收窄——误命中会白抓一帧（虽然不上传也不留存，但仍是采集）。 */
export const VISION_TRIGGER = new RegExp(
  '(那|这|前面(那个|这个)?|左边(那个)?|右边(那个)?|路边(那个)?)' +
  '.{0,4}(是什么|是啥|什么东西|什么牌子|什么车|什么花|什么树|什么建筑|什么标志)' +
  '|看看(这个|那个|前面)|帮我看看',
)

/** guard：这些说法归别的能力（POI 详情 / 卡片序号指代），不该触发抓帧。 */
export const VISION_GUARD = /这家|那家|第[一二三四五六七八九十\d]+个|详情|评分|人均|营业|怎么样|好不好/

/** 这句话要不要抓帧。与 agents/vision/manifest.yaml 的 route_hints 同口径（两侧同步改）。 */
export function needsFrame(text) {
  const t = (text || '').trim()
  if (!t) return false
  return VISION_TRIGGER.test(t) && !VISION_GUARD.test(t)
}

/**
 * 抓一帧并上传，返回 frame_id；任何失败返回空串（**由 Agent 侧诚实说「没拿到画面」**，
 * 不在这里弹错误打断对话）。
 *
 * @param {string} audioApi  llm-gateway 音频面地址
 * @param {object} deps      { getStream? } 可注入（测试/复用既有 mic 流）
 */
export async function captureFrame(audioApi, deps = {}) {
  let stream = null
  let ownsStream = false
  try {
    stream = deps.getStream ? await deps.getStream() : null
    if (!stream) {
      if (!navigator?.mediaDevices?.getUserMedia) return ''
      // PoC 没有车外摄像头：用设备摄像头扮演，卡片与设置文案恒显「模拟」。
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1280 } }, audio: false,
      })
      ownsStream = true
    }
    const blob = await grabJpeg(stream, deps)
    if (!blob) return ''
    const r = await fetch(`${audioApi}/api/vision/frame?mime=image/jpeg`,
      { method: 'POST', body: blob, headers: { 'Content-Type': 'image/jpeg' } })
    if (!r.ok) return ''
    return (await r.json()).frame_id || ''
  } catch {
    return ''
  } finally {
    // **用完立刻关摄像头**：单帧的语义就是拍完就走，绝不留着一个开着的视频轨。
    if (ownsStream && stream) stream.getTracks().forEach((t) => t.stop())
  }
}

async function grabJpeg(stream, deps = {}) {
  if (deps.grab) return deps.grab(stream)
  const video = document.createElement('video')
  video.srcObject = stream
  video.muted = true
  video.playsInline = true
  await video.play()
  // 等一帧真的解码出来（刚 play 时 videoWidth 可能还是 0）
  for (let i = 0; i < 20 && !video.videoWidth; i++) {
    await new Promise((r) => setTimeout(r, 25))
  }
  if (!video.videoWidth) return null
  const scale = Math.min(1, 1280 / video.videoWidth)
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(video.videoWidth * scale)
  canvas.height = Math.round(video.videoHeight * scale)
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)
  video.pause()
  video.srcObject = null
  return await new Promise((res) => canvas.toBlob(res, 'image/jpeg', 0.8))
}
