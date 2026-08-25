// 白名单守卫（实施计划 M0-4 ⛔）：共享面「台账 + 机器守」。三条断言：
//  ① mobile 源码里全部 `@shared/` 引用 ⊆ shared-allowlist.json 台账
//  ② 台账内每个文件不含 DOM/BOM 全局（window./document./localStorage/import.meta/navigator，
//     去注释后扫——pcmPlayer.mjs 的注入说明注释里合法地提到 window.AudioContext；
//     location.mjs 例外条款：允许文件含 navigator，但 mobile 源码不得出现 requestCurrentLocation）
//  ③ 台账里 phase 晚于 currentPhase 的模块不得被引用（阶段值在 json 顶层手动推进）
// 另守台账自身健康：每条 file 在 hmi/src 真实存在（防 typo——「能力从哪里声明」教训）。
import * as fs from 'fs'
import * as path from 'path'

const mobileRoot = path.resolve(__dirname, '..')
const hmiSrc = path.resolve(mobileRoot, '..', 'hmi', 'src')

interface AllowlistModule {
  file: string
  phase: string
  purpose: string
  notes: string
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

  test('② 台账内文件零 DOM/BOM 全局（location.mjs 的 navigator 例外）', () => {
    const banned: [string, RegExp][] = [
      ['window.', /\bwindow\s*\./],
      ['document.', /\bdocument\s*\./],
      ['localStorage', /\blocalStorage\b/],
      ['import.meta', /\bimport\s*\.\s*meta\b/],
    ]
    const offenders: string[] = []
    for (const m of allowlist.modules) {
      const full = path.join(hmiSrc, m.file)
      if (!fs.existsSync(full)) continue // 已由台账健康断言报错
      const code = stripComments(fs.readFileSync(full, 'utf8'))
      for (const [label, re] of banned) {
        if (re.test(code)) offenders.push(`${m.file}: ${label}`)
      }
      if (m.file !== 'location.mjs' && /\bnavigator\b/.test(code)) {
        offenders.push(`${m.file}: navigator`)
      }
    }
    expect(offenders).toEqual([])
  })

  test('② 例外条款：mobile 源码不得引用 requestCurrentLocation（它用 navigator）', () => {
    const offenders = sourceFiles.filter((f) =>
      /\brequestCurrentLocation\b/.test(fs.readFileSync(f, 'utf8')),
    )
    expect(offenders.map((f) => path.relative(mobileRoot, f))).toEqual([])
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
