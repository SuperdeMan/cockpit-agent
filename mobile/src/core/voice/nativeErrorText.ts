// 原生异常的人话（2026-09-21，D-08 真机 G-06 引擎成因分支露出来的）。
//
// Expo 把原生 CodedException 包成 DecoratedException 再扔给 JS，message 长这样：
//   Call to function 'Kws.load' has been rejected.
//   → Caused by: 唤醒词引擎加载失败（诊断替身：强制失败一次）
// 设置页开关下那一行原样打出来就是「免唤醒没有启动：Call to function 'Kws.load' has been rejected. → Caused by: …」
// ——包装是给开发者看的，用户要的只是最里层那句。`code`（KWS_WORKER_STUCK / KWS_DEBUG_LOAD_FAILED）不在
// message 里，分流仍按 code / name，不按文本。
const CAUSED_BY = /→ Caused by: /g

/** 取最里层的原因文本；没有包装就原样返回。空串 / 非 Error 按 String() 处理。 */
export function nativeErrorText(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  const parts = raw.split(CAUSED_BY)
  const last = parts[parts.length - 1]?.trim()
  return last || raw
}
