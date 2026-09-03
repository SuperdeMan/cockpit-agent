// manual 卡图片的两端共享守卫。后端只产 trusted PNG/JPEG data URI，渲染端仍做第二道
// 协议/体积/形状校验：未知 URI 不请求网络，SVG 不进入 DOM。
export const MAX_MANUAL_IMAGE_URI_CHARS = 900_000
export const MAX_MANUAL_IMAGES = 2

const PREFIX = /^data:image\/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})$/

export function manualImageUri(value) {
  if (typeof value !== 'string' || value.length > MAX_MANUAL_IMAGE_URI_CHARS) return ''
  const match = PREFIX.exec(value)
  if (!match || match[2].length % 4 !== 0) return ''
  return value
}

export function manualImages(card) {
  const source = Array.isArray(card?.images) ? card.images : []
  const out = []
  const seen = new Set()
  for (const raw of source) {
    if (!raw || typeof raw !== 'object') continue
    const dataUri = manualImageUri(raw.data_uri)
    if (!dataUri) continue
    const key = String(raw.asset_id || raw.sha256 || '')
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push({ ...raw, data_uri: dataUri })
    if (out.length >= MAX_MANUAL_IMAGES) break
  }
  return out
}
