// 助手气泡的匀速上屏（2026-09-18，features/chat/useRevealedText.ts 经 MessageBubble）。
// 钉五件事：① 挂载时全文直出（历史恢复 / 复用不重放）；② 流式中一簇长文到达 ⇒ 屏上先只多出几个字、
// 按节拍追、REVEAL_LAG_MS 内追平；③ streaming 落下时还没追平 ⇒ 尾巴追完而不是一次跳到位；
// ④ final 整段替换（剥 markdown）⇒ 直接跳到位；⑤ 一次性 final（只在 final 里到达的整段）也按节拍扫出、不一次蹦出
//（2026-09-18 用户：联网搜索等一次性到达的文字要与流式观感一致）；用户气泡不追。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView, Text } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
jest.mock('expo-clipboard', () => ({ setStringAsync: async () => true }))

import type { Msg } from '@shared/types.ts'
import { MessageBubble } from '@/features/chat/MessageBubble'
import { REVEAL_LAG_MS, REVEAL_TICK_MS } from '@/core/session/streamReveal'
import { StreamCursor } from '@/ui/aurora'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')

function bubble(msg: Msg) {
  return createElement(MessageBubble, { p, msg, confirmActive: false, loops: false, driving: false, onSend: jest.fn() } as never)
}
const shownText = (view: ReactTestRenderer): string => {
  const node = view.root.findAllByProps({ testID: 'bubble-text' }).find((n) => n.type === Text)!
  const kids = Array.isArray(node.props.children) ? node.props.children : [node.props.children]
  return kids.filter((k: unknown) => typeof k === 'string').join('')
}
const cursorShown = (view: ReactTestRenderer) => view.root.findAllByType(StreamCursor).length > 0
const tick = (n = 1) => act(() => { jest.advanceTimersByTime(REVEAL_TICK_MS * n) })

beforeEach(() => { jest.useFakeTimers() })
afterEach(() => { jest.useRealTimers() })

const LONG = '很久以前，在一座被海雾常年笼罩的小岛上，住着一个叫阿澈的年轻人。'

test('挂载时全文直出；流式中一簇长文到达 ⇒ 先只多出几个字、按节拍追、LAG 内追平；光标跟到尾', () => {
  let view!: ReactTestRenderer
  act(() => { view = create(bubble({ id: 'a', role: 'assistant', text: '好的，', streaming: true })) })
  expect(shownText(view)).toBe('好的，')
  act(() => { view.update(bubble({ id: 'a', role: 'assistant', text: '好的，' + LONG, streaming: true })) })
  // 到达那一帧：屏上还是原文（追是从下一拍开始的）
  expect(shownText(view)).toBe('好的，')
  tick()
  const afterOne = shownText(view)
  expect(afterOne.length).toBeGreaterThan('好的，'.length)
  expect(afterOne.length).toBeLessThan(('好的，' + LONG).length)
  expect(('好的，' + LONG).startsWith(afterOne)).toBe(true)
  expect(cursorShown(view)).toBe(true)
  tick(Math.ceil(REVEAL_LAG_MS / REVEAL_TICK_MS) + 1)
  expect(shownText(view)).toBe('好的，' + LONG)
  act(() => { view.unmount() })
})

test('streaming 落下时没追平 ⇒ 尾巴照节拍追完，光标留到追平；追平后定时器停', () => {
  let view!: ReactTestRenderer
  act(() => { view = create(bubble({ id: 'a', role: 'assistant', text: '好', streaming: true })) })
  act(() => { view.update(bubble({ id: 'a', role: 'assistant', text: '好' + LONG, streaming: true })) })
  tick()
  const mid = shownText(view)
  expect(mid.length).toBeLessThan(('好' + LONG).length)
  // final：同一段文本、streaming=false
  act(() => { view.update(bubble({ id: 'a', role: 'assistant', text: '好' + LONG, streaming: false })) })
  expect(shownText(view)).toBe(mid)            // 没有一次跳到位
  expect(cursorShown(view)).toBe(true)
  tick(Math.ceil(REVEAL_LAG_MS / REVEAL_TICK_MS) + 1)
  expect(shownText(view)).toBe('好' + LONG)
  expect(cursorShown(view)).toBe(false)
  expect(jest.getTimerCount()).toBe(0)
  act(() => { view.unmount() })
})

test('final 整段替换（不是已显示文本的延长）⇒ 直接跳到位', () => {
  let view!: ReactTestRenderer
  act(() => { view = create(bubble({ id: 'a', role: 'assistant', text: '**深圳**的历史', streaming: true })) })
  act(() => { view.update(bubble({ id: 'a', role: 'assistant', text: '深圳的历史可以追溯到六千年前。', streaming: false })) })
  expect(shownText(view)).toBe('深圳的历史可以追溯到六千年前。')
  expect(cursorShown(view)).toBe(false)
  act(() => { view.unmount() })
})

test('一次性 final（只在 final 里到达的整段）也按节拍扫出：到达那一帧不是全文，LAG 内追平；用户气泡不追', () => {
  let view!: ReactTestRenderer
  act(() => { view = create(bubble({ id: 'a', role: 'assistant', text: '', pending: true })) })
  act(() => { view.update(bubble({ id: 'a', role: 'assistant', text: LONG, pending: false, streaming: false })) })
  expect(shownText(view)).toBe('')
  tick()
  const first = shownText(view)
  expect(first.length).toBeGreaterThan(0)
  expect(first.length).toBeLessThan(LONG.length)
  expect(cursorShown(view)).toBe(true)              // 还在长 ⇒ 光标在
  tick(Math.ceil(REVEAL_LAG_MS / REVEAL_TICK_MS) + 1)
  expect(shownText(view)).toBe(LONG)
  expect(cursorShown(view)).toBe(false)
  act(() => { view.unmount() })

  let user!: ReactTestRenderer
  act(() => { user = create(bubble({ id: 'u', role: 'user', text: '给我' })) })
  act(() => { user.update(bubble({ id: 'u', role: 'user', text: '给我讲一个很长的故事' })) })
  const node = user.root.findAll((n) => n.type === Text && typeof n.props.children?.[0] === 'string')[0]
  expect(node.props.children[0]).toBe('给我讲一个很长的故事')
  act(() => { user.unmount() })
})

test('同一个气泡组件换了消息 id（列表复用）⇒ 直出新消息全文，不把它当成上一条的延长', () => {
  let view!: ReactTestRenderer
  act(() => { view = create(bubble({ id: 'a', role: 'assistant', text: '好的', streaming: false })) })
  act(() => { view.update(bubble({ id: 'b', role: 'assistant', text: '好的' + LONG, streaming: true })) })
  expect(shownText(view)).toBe('好的' + LONG)
  act(() => { view.unmount() })
})

test('成批到达（每 600ms 一批）经 hook：批与批之间显示持续在长，不是扫完一批就停', () => {
  let view!: ReactTestRenderer
  const batch = '字'.repeat(60)
  act(() => { view = create(bubble({ id: 'a', role: 'assistant', text: '', pending: true })) })
  let text = ''
  const stalls: number[] = []
  for (let b = 1; b <= 6; b += 1) {
    text += batch
    const t = text
    act(() => { view.update(bubble({ id: 'a', role: 'assistant', text: t, streaming: true })) })
    // 600ms 内每拍看一眼：第三批起，只要没追平，每拍都得在动
    let prev = shownText(view).length
    for (let i = 0; i < Math.floor(600 / REVEAL_TICK_MS); i += 1) {
      tick()
      const now = shownText(view).length
      if (b >= 3 && now < t.length && now === prev) stalls.push(b)
      prev = now
    }
  }
  expect(stalls).toEqual([])
  act(() => { view.update(bubble({ id: 'a', role: 'assistant', text, streaming: false })) })
  tick(60)
  expect(shownText(view)).toBe(text)
  act(() => { view.unmount() })
})
