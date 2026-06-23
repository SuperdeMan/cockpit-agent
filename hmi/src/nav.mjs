// 导航候选的口语序号选择解析。
// 在上一条 poi_list（导航/充电站候选）之后，用户说「第一个/第二个/去第三个/2」时，
// 解析出候选下标（0-based），由 HMI 改写为「导航去{该候选名称}」再发后端。
// 非序号选择返回 -1（走正常分发，不劫持普通查询）。

const _CN_NUM = {
  一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10,
}

export function poiSelectionIndex(text) {
  const t = String(text || '').trim()
  const m = t.match(/^(?:去|导航(?:去|到)?)?第\s*([0-9一二两三四五六七八九十]+)\s*个?$/)
  if (m) {
    const raw = m[1]
    const n = /^[0-9]+$/.test(raw) ? parseInt(raw, 10) : (_CN_NUM[raw] || 0)
    return n > 0 ? n - 1 : -1
  }
  if (/^[0-9]{1,2}$/.test(t)) {
    const n = parseInt(t, 10)
    return n > 0 ? n - 1 : -1
  }
  return -1
}

// 「换一批/换一个/还有别的」类表达：对上一条就近候选翻页换结果（需有活跃候选上下文才生效）。
const _REFRESH_RE = /^(换一?批|换一个|换一换|换批|再换一?|下一批|还有(别的|其他|没|吗)|有没有别的|换别的|都不满意)/

export function isRefreshRequest(text) {
  return _REFRESH_RE.test(String(text || '').trim())
}
