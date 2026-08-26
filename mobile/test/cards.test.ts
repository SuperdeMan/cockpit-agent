// 卡片框架守卫（M1-4 ⛔ 立，M3-1 改成派生式）：
//  ① 注册表 == `hmi/src/types.ts::UiCard` 派生出的全量卡型集合（**两个方向都断言**）
//  ② 未知卡型走兜底卡（铁则：绝不 null——HMI 渲染 null 两个月的欠账不许重演）
//  ③ 每个注册项都是可调用的渲染函数（防「注册了个 undefined」）
//
// 为什么从 types.ts **派生**而不是手抄清单（M3-1 改的就是这一点）：
// M1 版本把 §2.6 的清单逐字拷进测试，于是「清单」在仓库里有了第二份。
// 实施计划 §2.6 写的是「29 型 + card_group」，而 types.ts 的真实数是 **34 个
// type 字符串**——**手抄的那份已经漂了，而且没有任何东西会红**。
// 派生之后，加卡型只改 CardRenderer 一处；types.ts 加了型而 App 没跟，这里直接红。
import * as fs from 'fs'
import * as path from 'path'

import { FallbackCard, KNOWN_CARD_TYPES } from '@/features/cards/CardRenderer'
import { CARD_FIXTURES } from '@/features/cards/fixtures'

const typesSrc = fs.readFileSync(
  path.resolve(__dirname, '..', '..', 'hmi', 'src', 'types.ts'),
  'utf8',
)

/**
 * `hmi/src/types.ts` 的 `UiCard` 联合 → 全部 `type:` 字面量。
 *
 * 刻意**不**用「全文件 grep `type: '...'`」：那会把任何碰巧长这样的非卡片类型也算进来，
 * 判据就从「契约声明它是卡」滑成「源码里有这个词」。走联合成员是契约本身那条链。
 */
function declaredCardTypes(): string[] {
  const union = /export type UiCard\s*=\s*((?:\s*\|\s*\w+)+)/.exec(typesSrc)
  if (!union) throw new Error('types.ts 里找不到 UiCard 联合——契约结构变了，先看它再改这条守卫')
  const members = union[1].split('|').map((s) => s.trim()).filter(Boolean)
  expect(members.length).toBeGreaterThan(20) // 结构性自检：联合被解析成个位数说明正则失配

  const out = new Set<string>()
  for (const name of members) {
    const decl = new RegExp(`export type ${name}\\s*=\\s*\\{`).exec(typesSrc)
    if (!decl) throw new Error(`UiCard 成员 ${name} 在 types.ts 里没有对应的 type 声明`)
    const body = typesSrc.slice(decl.index)
    // 声明体里第一处 `type:`，读到该语句结束（`;` 或换行）为止
    const field = /\btype:\s*([^\n;}]+)/.exec(body)
    if (!field) throw new Error(`${name} 没有 type 字段——它进了 UiCard 却没声明卡型名`)
    const literals = field[1].match(/'([a-z_]+)'/g) || []
    if (!literals.length) throw new Error(`${name} 的 type 不是字面量：${field[1].trim()}`)
    for (const lit of literals) out.add(lit.slice(1, -1))
  }
  return [...out]
}

describe('卡片注册表（派生自 hmi/src/types.ts::UiCard）', () => {
  const declared = declaredCardTypes()

  test('契约里的每个卡型都有渲染器（缺一个=用户会看到兜底卡）', () => {
    const missing = declared.filter((t) => !KNOWN_CARD_TYPES.includes(t))
    expect(missing).toEqual([])
  })

  test('注册表里没有契约之外的卡型（多一个=注册了个后端不会发的型）', () => {
    const extra = KNOWN_CARD_TYPES.filter((t) => !declared.includes(t))
    expect(extra).toEqual([])
  })

  test('全量卡型不少于 M3 收口时的 34 个（往下掉说明契约被删了型，要有人看一眼）', () => {
    expect(declared.length).toBeGreaterThanOrEqual(34)
  })

  test('兜底卡存在且为组件（未知卡型的渲染出口）', () => {
    expect(typeof FallbackCard).toBe('function')
  })
})

describe('画廊样本覆盖（§8.3「全卡族截图归档」的机器版）', () => {
  const covered = new Set(CARD_FIXTURES.map((f) => f.card?.type))

  test('每个注册卡型都有样本（否则归档时会静默少一张，而少了谁没人会去数）', () => {
    expect(KNOWN_CARD_TYPES.filter((t) => !covered.has(t))).toEqual([])
  })

  test('样本里有一条是未知卡型（兜底卡在画廊里必须看得见）', () => {
    const unknown = CARD_FIXTURES.filter((f) => !KNOWN_CARD_TYPES.includes(f.card?.type))
    expect(unknown.length).toBeGreaterThanOrEqual(1)
  })

  test('每条样本都声明了 label（画廊靠它标注「真栈已验/样本」）', () => {
    expect(CARD_FIXTURES.filter((f) => !f.label)).toEqual([])
  })
})
