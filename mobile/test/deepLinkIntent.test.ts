// 外部深链的路由判据（AR09 / F1′）。
//
// 被测的是「深链是目的地、不是再开一份」这条规则，以及它的两个边界：
// 认不出的 URL 必须原样交回，冷启动必须交回 router。
//
// ⚠ 本文件末尾的栈模拟是**我对 StackRouter 的模型**，不是 StackRouter 本身。它能证明
// 判据的意图（栈深有界），证明不了真机行为——真机证据靠 `scripts/probe_ui_perf.py`
// 的 Views/PSS 读数，两者不可互相顶替。
import {
  intentSteps,
  isHome,
  normalizeIntentHref,
  planIntent,
  splitAppUrl,
  type IntentStep,
} from '@/core/nav/deepLink'

describe('splitAppUrl', () => {
  it('host 位与 path 位两种写法等价', () => {
    expect(splitAppUrl('xiaozhou://voice')).toEqual({ path: '/voice', query: '' })
    expect(splitAppUrl('xiaozhou:///voice')).toEqual({ path: '/voice', query: '' })
  })

  it('保留 query，去掉 fragment 与结尾斜杠', () => {
    expect(splitAppUrl('xiaozhou:///map?points=%5B%5D&title=x')).toEqual({
      path: '/map',
      query: 'points=%5B%5D&title=x',
    })
    expect(splitAppUrl('xiaozhou:///settings/')).toEqual({ path: '/settings', query: '' })
    expect(splitAppUrl('xiaozhou:///settings#frag')).toEqual({ path: '/settings', query: '' })
    expect(splitAppUrl('xiaozhou://')).toEqual({ path: '/', query: '' })
  })

  it('不是本 App 的 scheme 一律返回 null（不猜）', () => {
    expect(splitAppUrl('exp://192.168.1.2:8081/--/settings')).toBeNull()
    expect(splitAppUrl('https://example.com/settings')).toBeNull()
    expect(splitAppUrl('/settings')).toBeNull()
    expect(splitAppUrl('')).toBeNull()
  })
})

describe('normalizeIntentHref', () => {
  it('voice 是指令不是目的地：规范化成对话页的参数', () => {
    expect(normalizeIntentHref('xiaozhou://voice')).toBe('/?voice=1')
    expect(normalizeIntentHref('xiaozhou:///voice')).toBe('/?voice=1')
  })

  it('voice 深链自带 query 时合并而不覆盖，且已有 voice 不重复加', () => {
    expect(normalizeIntentHref('xiaozhou://voice?from=shortcut')).toBe('/?from=shortcut&voice=1')
    expect(normalizeIntentHref('xiaozhou://voice?voice=1')).toBe('/?voice=1')
  })

  it('其它路由原样通过', () => {
    expect(normalizeIntentHref('xiaozhou://vehicle')).toBe('/vehicle')
    expect(normalizeIntentHref('xiaozhou:///settings')).toBe('/settings')
    expect(normalizeIntentHref('xiaozhou:///map?points=%5B%5D')).toBe('/map?points=%5B%5D')
    expect(normalizeIntentHref('xiaozhou:///')).toBe('/')
  })

  it('认不出的 URL 逐字返回', () => {
    const dev = 'exp://192.168.1.2:8081/--/settings?x=1'
    expect(normalizeIntentHref(dev)).toBe(dev)
  })
})

describe('planIntent', () => {
  it('冷启动一律交回 router（那时栈还不存在，插手只会建歪）', () => {
    expect(planIntent('xiaozhou://voice', { initial: true })).toEqual({
      kind: 'handoff',
      href: '/?voice=1',
    })
    expect(planIntent('xiaozhou:///settings', { initial: true })).toEqual({
      kind: 'handoff',
      href: '/settings',
    })
  })

  it('运行中收到的、认得出的目的地由我们接管', () => {
    expect(planIntent('xiaozhou://voice', { initial: false })).toEqual({
      kind: 'enter',
      href: '/?voice=1',
      home: true,
    })
    expect(planIntent('xiaozhou://vehicle', { initial: false })).toEqual({
      kind: 'enter',
      href: '/vehicle',
      home: false,
    })
  })

  it('认不出的 URL 无论冷热都交回，且不改一个字', () => {
    const dev = 'exp://192.168.1.2:8081/--/settings'
    expect(planIntent(dev, { initial: false })).toEqual({ kind: 'handoff', href: dev })
    expect(planIntent(dev, { initial: true })).toEqual({ kind: 'handoff', href: dev })
  })
})

describe('isHome', () => {
  it('只有对话页算 home，带参数也算', () => {
    expect(isHome('/')).toBe(true)
    expect(isHome('/?voice=1')).toBe(true)
    expect(isHome('/settings')).toBe(false)
    expect(isHome('/map?points=%5B%5D')).toBe(false)
  })
})

describe('intentSteps', () => {
  it('目的地是对话页：一步 dismissTo，把它上面的都弹掉并带上新参数', () => {
    expect(intentSteps({ kind: 'enter', href: '/?voice=1', home: true }, true)).toEqual([
      { op: 'dismissTo', href: '/?voice=1' },
    ])
  })

  it('目的地不是对话页：先回对话页再进去，「返回」才落在对话页', () => {
    expect(intentSteps({ kind: 'enter', href: '/vehicle', home: false }, true)).toEqual([
      { op: 'dismissTo', href: '/' },
      { op: 'navigate', href: '/vehicle' },
    ])
  })

  it('栈里只有一层时不做多余的弹栈', () => {
    expect(intentSteps({ kind: 'enter', href: '/vehicle', home: false }, false)).toEqual([
      { op: 'navigate', href: '/vehicle' },
    ])
  })
})

// ── 栈深有界（这条判据是本次修复的正题）────────────────────────────────────────
// 模型按 2026-09-10 现读的 expo-router 57 `StackClient`/`StackRouter` 写：
//   · NAVIGATE：目标 == 栈顶则替换栈顶，否则推一屏；
//   · POP_TO：栈里有该路由则弹到它（并换参数），没有则用它替换当前栈顶。
type Frame = { path: string; href: string }

function applyStep(stack: Frame[], step: IntentStep): Frame[] {
  const path = step.href.split('?')[0] || '/'
  const frame = { path, href: step.href }
  if (step.op === 'navigate') {
    const top = stack[stack.length - 1]
    return top && top.path === path ? [...stack.slice(0, -1), frame] : [...stack, frame]
  }
  const at = stack.map((f) => f.path).lastIndexOf(path)
  return at >= 0 ? [...stack.slice(0, at), frame] : [...stack.slice(0, -1), frame]
}

function arrive(stack: Frame[], url: string): Frame[] {
  const plan = planIntent(url, { initial: false })
  if (plan.kind === 'handoff') throw new Error(`本用例只喂认得出的深链：${url}`)
  return intentSteps(plan, stack.length > 1).reduce(applyStep, stack)
}

describe('外部深链反复到达时栈深有界', () => {
  const SHORTCUTS = ['xiaozhou://voice', 'xiaozhou://vehicle']

  it('连点同一个 Shortcut 50 次，栈深恒定', () => {
    for (const url of SHORTCUTS) {
      let stack: Frame[] = [{ path: '/', href: '/' }]
      const depths = new Set<number>()
      for (let i = 0; i < 50; i++) {
        stack = arrive(stack, url)
        depths.add(stack.length)
      }
      expect(Math.max(...depths)).toBeLessThanOrEqual(2)
    }
  })

  it('两个 Shortcut 交替 50 次也不堆积（这正是真机上会涨的那种形态）', () => {
    let stack: Frame[] = [{ path: '/', href: '/' }]
    let max = stack.length
    for (let i = 0; i < 50; i++) {
      stack = arrive(stack, SHORTCUTS[i % SHORTCUTS.length])
      max = Math.max(max, stack.length)
    }
    expect(max).toBeLessThanOrEqual(2)
    expect(stack.filter((f) => f.path === '/').length).toBe(1)
  })

  it('反向验证：不做规范化、按 router 原样 NAVIGATE 就会线性堆积', () => {
    // 这是修复前的形态：voice 先推一屏、再被 replace 换成第二个对话页。
    let stack: Frame[] = [{ path: '/', href: '/' }]
    for (let i = 0; i < 20; i++) {
      stack = applyStep(stack, { op: 'navigate', href: '/voice' })
      stack = [...stack.slice(0, -1), { path: '/', href: '/?voice=1' }] // Redirect 的 replace
    }
    expect(stack.length).toBe(21)
    expect(stack.filter((f) => f.path === '/').length).toBe(21)
  })

  it('语音层参数每次到达都真的重新出现（否则第二次点「说话」就不升层了）', () => {
    let stack: Frame[] = [{ path: '/', href: '/' }]
    for (let i = 0; i < 3; i++) {
      stack = arrive(stack, 'xiaozhou://voice')
      expect(stack[stack.length - 1].href).toBe('/?voice=1')
      // ChatScreen 消费后把参数清掉（见 ChatScreen 的 voiceParam effect）
      stack = [...stack.slice(0, -1), { path: '/', href: '/' }]
    }
  })
})
