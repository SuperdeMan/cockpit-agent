// 商户/支付族守卫（M3-1）。两组断言，各自防一类事故：
//
// ① **共享安全闸在 RN 的 URL 实现下仍然 fail closed。**
//    `merchantUi.mjs` 的 `safeHttpsUrl` 用 `new URL()` 挡协议与凭据，而
//    **jest 里的全局 URL 是 Node 的规范实现，真机上是 react-native/Libraries/Blob/URL
//    那份残缺实现**——两者对垃圾串的处置就不一样（Node 抛 TypeError，RN 不抛、
//    protocol 返回空串）。直接 `expect(paymentPresentation(...))` 只会测到 Node 那份，
//    **测试替被测系统提供了一个它在设备上没有的前提**（CLAUDE.md §6 那条纪律）。
//    所以这里把全局 URL 换成 RN 那份再跑，测的才是手机上真正会发生的事。
//
// ② `decodeSvgDataUri` 的解码与拒绝边界（付款码渲染的唯一入口）。
import { URL as RNURL } from 'react-native/Libraries/Blob/URL'

import { merchantImageUrl, paymentPresentation } from '@shared/merchantUi.mjs'

import { decodeSvgDataUri } from '@/features/cards/merchantCards'

/** 在 RN 的 URL 实现下执行——测的是设备行为，不是 Node 行为 */
function underRNUrl<T>(fn: () => T): T {
  const saved = globalThis.URL
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).URL = RNURL
  try {
    return fn()
  } finally {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(globalThis as any).URL = saved
  }
}

describe('RN 的 URL 实现下，共享安全闸仍 fail closed', () => {
  test('前提自检：jest 的全局 URL 与 RN 的不是同一份（这条红了说明下面的测试白测）', () => {
    let nodeThrows = false
    try {
      new URL('junkjunk')
    } catch {
      nodeThrows = true
    }
    expect(nodeThrows).toBe(true) // Node：抛
    expect(new RNURL('junkjunk').protocol).toBe('') // RN：不抛，protocol 空
  })

  test('正常 https 支付链接放行', () => {
    const r = underRNUrl(() => paymentPresentation({ pay_url: 'https://pay.example.com/x?id=1' }))
    expect(r.hasQr).toBe(false)
    expect(r.safeUrl).toBe('https://pay.example.com/x?id=1')
  })

  test.each([
    ['javascript: 伪协议', 'javascript:alert(1)'],
    ['明文 http', 'http://pay.example.com/x'],
    ['垃圾串', 'junkjunk'],
    ['带凭据', 'https://user:pw@pay.example.com/x'],
    ['空', ''],
  ])('%s 一律拦掉（safeUrl 为空串）', (_label, url) => {
    const r = underRNUrl(() => paymentPresentation({ pay_url: url }))
    expect(r.safeUrl).toBe('')
    expect(underRNUrl(() => merchantImageUrl(url))).toBe('')
  })

  test('有 qr_svg 时走扫码分支（hasQr 只认 data:image/svg+xml 前缀）', () => {
    expect(underRNUrl(() => paymentPresentation({ qr_svg: 'data:image/svg+xml;base64,AAAA' })).hasQr).toBe(true)
    expect(underRNUrl(() => paymentPresentation({ qr_svg: 'https://evil.example/x.svg' })).hasQr).toBe(false)
  })

  // ⚠ **已知差异，2026-08-27 M3-1 取证，故意钉成测试而不是「修掉」**：
  // RN 的 `get username()` 是 `/^https?:\/\/([^:@]+)(?::[^@]*)?@/`——`[^:@]+` **不在
  // 路径分隔符处停**，于是 URL 里**任何位置**出现 `@`（路径、查询串里都算）都被读成
  // userinfo，`safeHttpsUrl` 判定「带凭据」并拒掉。浏览器上 `new URL()` 是规范实现，
  // 同一个 URL 照常放行 ⇒ **同一张卡，HMI 显示、App 不显示**。
  //
  // 为什么不改：① 方向是 fail closed（拒绝而不是放行），不是安全漏洞；
  // ② `merchantUi.mjs` 在浏览器上是**对的**，这不是 hmi 的 bug 而是 RN 的实现缺陷，
  // 单方面改共享安全闸得先有人裁；③ 真实商户 URL 里到底有没有 `@` 我没有观测数据
  // （仓库样本都没有，但 `@` 做图片尺寸变体是国内 CDN 的常见惯例）。
  // ⇒ 先让它**可见**：这条测试红了说明有人动了判据，绿着说明差异还在。
  //    代价明说：`pay_url` 若含 `@`，App 会显示「支付入口暂不可用」——**那是一句假话**。
  //    真机上一旦观测到含 `@` 的商户 URL，这条就升级成待修（修法=改判 authority 段
  //    而不读 getter，两端都对）。
  test.each([
    ['查询串里的 @（无端口）', 'https://pay.example.com/x?e=a@b.com'],
    ['路径里的 @（CDN 尺寸变体惯例）', 'https://img04.luckincoffeecdn.com/pic/1262.png@200w'],
  ])('%s：RN 拒、Node 放行——差异在此留痕', (_label, url) => {
    expect(underRNUrl(() => paymentPresentation({ pay_url: url })).safeUrl).toBe('') // 设备行为
    expect(paymentPresentation({ pay_url: url }).safeUrl).toBe(url) // 浏览器行为
  })
})

describe('decodeSvgDataUri（付款码的唯一渲染入口）', () => {
  const b64 = (s: string) => Buffer.from(s, 'utf8').toString('base64')

  test('解出网关那种 SVG（带 XML 声明的 SvgPathImage 产物）', () => {
    const svg = `<?xml version='1.0' encoding='UTF-8'?><svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1h-1z"/></svg>`
    expect(decodeSvgDataUri(`data:image/svg+xml;base64,${b64(svg)}`)).toBe(svg)
  })

  test('多字节 UTF-8 原样还原（不假设产生方永远只发 ASCII）', () => {
    const svg = '<svg><title>付款码·¥15</title></svg>'
    expect(decodeSvgDataUri(`data:image/svg+xml;base64,${b64(svg)}`)).toBe(svg)
  })

  test.each([
    ['非 data URI', 'https://evil.example/x.svg'],
    ['别的 data 类型', `data:image/png;base64,${b64('<svg/>')}`],
    ['解出来不是 SVG', `data:image/svg+xml;base64,${b64('<html>nope</html>')}`],
    ['空串', ''],
  ])('%s → 空串（调用方据此降级到安全链接，不渲半张码）', (_label, input) => {
    expect(decodeSvgDataUri(input)).toBe('')
  })
})
