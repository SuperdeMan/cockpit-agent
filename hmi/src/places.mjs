// 常用地点（家/公司/学校）回显的纯逻辑：解析 memory 画像 profile.places。
// 与 audio.ts 的网络层分离，便于单测（HMI 测试约定：纯函数放 .mjs + .test.mjs）。

// 展示顺序与文案。hint 是“怎么用语音设置”，未设置时提示用户。
export const PLACE_DEFS = [
  { key: 'home', label: '家', icon: '🏠', hint: '我家在XX' },
  { key: 'company', label: '公司', icon: '🏢', hint: '把公司设成XX' },
  { key: 'school', label: '学校', icon: '🎓', hint: '学校在XX' },
]

/**
 * 把 /api/memory/context 的 profile.places 值解析成 {home,company,...} map。
 * 入参可能是 JSON 字符串、已解析对象，或缺失/脏数据——一律安全返回 {}。
 */
export function parsePlacesValue(raw) {
  if (!raw) return {}
  let obj = raw
  if (typeof raw === 'string') {
    try {
      obj = JSON.parse(raw)
    } catch {
      return {}
    }
  }
  return obj && typeof obj === 'object' && !Array.isArray(obj) ? obj : {}
}

/** 一条地点是否“已设置”（有地址或名称）。 */
export function isPlaceSet(place) {
  return !!(place && (place.address || place.name))
}

/** 已设置地点的展示文案：名称 · 地址（缺一则只显示另一个）。 */
export function formatPlace(place) {
  if (!isPlaceSet(place)) return ''
  const name = place.name || ''
  const addr = place.address || ''
  if (name && addr) return `${name} · ${addr}`
  return name || addr
}
