// 白名单守卫（实施计划 M0-4 ⛔）：共享面「台账 + 机器守」。三条断言：
//  ① mobile 源码里全部 `@shared/` 引用 ⊆ shared-allowlist.json 台账
//  ② 台账内每个文件不含 DOM/BOM 全局（window./document./localStorage/import.meta/navigator，
//     去注释后扫——pcmPlayer.mjs 的注入说明注释里合法地提到 window.AudioContext）
//  ③ 台账里 phase 晚于 currentPhase 的模块不得被引用（阶段值在 json 顶层手动推进）
// 另守台账自身健康：每条 file 在 hmi/src 真实存在（防 typo——「能力从哪里声明」教训）。
//
// **例外条款从台账派生，不在本文件里另写一份**（M4 改）。原来 location.mjs 的
// navigator 例外是硬编码在这里的两处字面量，于是「哪些模块有例外、例外是什么」在仓库里
// 有了第二份——而漂掉的那份没有任何东西会红（M3 那 34 个卡型是同一形态）。
// 现在每条例外写在 json 的 `domException`：`globals` 是允许出现的 DOM 全局，
// `forbiddenSymbols` 是 mobile 源码禁引的符号（那才是真正的防线：文件里有 DOM 不要紧，
// **App 不去引那个用 DOM 的导出**才要紧）。
import * as fs from 'fs'
import * as path from 'path'

const mobileRoot = path.resolve(__dirname, '..')
const hmiSrc = path.resolve(mobileRoot, '..', 'hmi', 'src')

interface DomException {
  /** 允许在该文件里出现的 DOM/BOM 全局（其余仍禁） */
  globals: string[]
  /** mobile 源码禁止引用的符号——例外的真正防线在这一条 */
  forbiddenSymbols: string[]
  why: string
}
interface AllowlistModule {
  file: string
  phase: string
  purpose: string
  notes: string
  domException?: DomException
}
interface Allowlist {
  currentPhase: string
  phaseOrder: string[]
  modules: AllowlistModule[]
}

const allowlist: Allowlist = JSON.parse(
  fs.readFileSync(path.join(mobileRoot, 'shared-allowlist.json'), 'utf8'),
)

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(full))
    else if (/\.(ts|tsx|js|jsx|mjs)$/.test(entry.name)) out.push(full)
  }
  return out
}

const sourceFiles = walk(path.join(mobileRoot, 'src'))

/** mobile 源码中实际引用的共享模块文件名集合（如 'ws.mjs'） */
function collectSharedImports(): Map<string, string[]> {
  const usedBy = new Map<string, string[]>()
  for (const file of sourceFiles) {
    const code = fs.readFileSync(file, 'utf8')
    for (const m of code.matchAll(/@shared\/([A-Za-z0-9_./-]+)/g)) {
      const mod = m[1]
      const list = usedBy.get(mod) ?? []
      list.push(path.relative(mobileRoot, file))
      usedBy.set(mod, list)
    }
  }
  return usedBy
}

/** 去掉 // 行注释与 /* *\/ 块注释（守卫扫的是代码引用，不是文档提及） */
function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
}

describe('shared allowlist guard', () => {
  const allowedFiles = new Set(allowlist.modules.map((m) => m.file))
  const usedBy = collectSharedImports()

  test('台账健康：每条 file 在 hmi/src 真实存在、phase 合法、无重复', () => {
    const seen = new Set<string>()
    for (const m of allowlist.modules) {
      expect(fs.existsSync(path.join(hmiSrc, m.file))).toBe(true)
      expect(allowlist.phaseOrder).toContain(m.phase)
      expect(seen.has(m.file)).toBe(false)
      seen.add(m.file)
    }
    expect(allowlist.phaseOrder).toContain(allowlist.currentPhase)
  })

  test('① 实际 @shared 引用 ⊆ 台账', () => {
    const offenders = [...usedBy.keys()].filter((mod) => !allowedFiles.has(mod))
    expect(
      offenders.map((mod) => `${mod} (used by ${usedBy.get(mod)!.join(', ')})`),
    ).toEqual([])
  })

  test('② 台账内文件零 DOM/BOM 全局（例外由台账的 domException 声明）', () => {
    const banned: [string, RegExp][] = [
      ['window.', /\bwindow\s*\./],
      ['document.', /\bdocument\s*\./],
      ['localStorage', /\blocalStorage\b/],
      ['import.meta', /\bimport\s*\.\s*meta\b/],
      ['navigator', /\bnavigator\b/],
    ]
    const offenders: string[] = []
    for (const m of allowlist.modules) {
      const full = path.join(hmiSrc, m.file)
      if (!fs.existsSync(full)) continue // 已由台账健康断言报错
      const code = stripComments(fs.readFileSync(full, 'utf8'))
      const allowed = new Set(m.domException?.globals ?? [])
      for (const [label, re] of banned) {
        if (allowed.has(label)) continue
        if (re.test(code)) offenders.push(`${m.file}: ${label}`)
      }
    }
    expect(offenders).toEqual([])
  })

  test('② 例外条款：mobile 源码不得引用台账声明的 forbiddenSymbols', () => {
    const offenders: string[] = []
    for (const m of allowlist.modules) {
      for (const sym of m.domException?.forbiddenSymbols ?? []) {
        const re = new RegExp(`\\b${sym}\\b`)
        for (const f of sourceFiles) {
          // 同 hmi 侧口径：**去注释后扫**。注释里写「为什么不引它」正是我们要的东西，
          // 而 import 语句不可能藏在注释里，所以这不削弱守卫。
          if (re.test(stripComments(fs.readFileSync(f, 'utf8')))) {
            offenders.push(`${path.relative(mobileRoot, f)}: ${sym} (${m.file})`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })

  test('例外必须被用上：声明了 domException.globals 的文件，那个全局要真的在里面', () => {
    // 反向守：例外过期了（上游把 navigator 拿掉了）却还挂在台账上 ⇒ 一个永远为真的豁免，
    // 下次有人往那个文件里加 DOM 就悄悄放行了。**豁免也要有到期检查。**
    const stale: string[] = []
    for (const m of allowlist.modules) {
      const globals = m.domException?.globals ?? []
      if (!globals.length) continue
      const full = path.join(hmiSrc, m.file)
      if (!fs.existsSync(full)) continue
      const code = stripComments(fs.readFileSync(full, 'utf8'))
      for (const g of globals) {
        const re = new RegExp(`\\b${g.replace('.', '\\s*\\.')}`)
        if (!re.test(code)) stale.push(`${m.file}: 豁免了 ${g} 但文件里没有它`)
      }
    }
    expect(stale).toEqual([])
  })

  test('③ phase 晚于当前阶段的模块不得被引用', () => {
    const currentIdx = allowlist.phaseOrder.indexOf(allowlist.currentPhase)
    const phaseOf = new Map(allowlist.modules.map((m) => [m.file, m.phase]))
    const offenders = [...usedBy.keys()].filter((mod) => {
      const phase = phaseOf.get(mod)
      if (!phase) return false // ① 已覆盖台账外引用
      return allowlist.phaseOrder.indexOf(phase) > currentIdx
    })
    expect(
      offenders.map(
        (mod) => `${mod} (phase ${phaseOf.get(mod)}, current ${allowlist.currentPhase})`,
      ),
    ).toEqual([])
  })
})
