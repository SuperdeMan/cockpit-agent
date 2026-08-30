// mobile/test/turnView.test.ts
// 「当前这一轮」= 最后一条用户气泡 + 其后的助手气泡（语音层读它；主动播报与到期留痕不算这一轮的回答）。
import type { Msg } from '@shared/types.ts'

import { currentTurn, isAside, isProactive } from '@/core/session/turnView'

const u = (id: string, text: string): Msg => ({ id, role: 'user', text })
const a = (id: string, text: string, extra: Partial<Msg> = {}): Msg => ({ id, role: 'assistant', text, ...extra })

test('最后一条用户气泡 + 其后的助手气泡', () => {
  const t = currentTurn([u('u1', '你好'), a('a1', '你好呀'), u('u2', '天气'), a('a2', '晴')])
  expect(t.user?.id).toBe('u2')
  expect(t.assistant?.id).toBe('a2')
})

test('用户刚说完、助手还没答：assistant=null，且不许把上一轮的助手气泡当成回答', () => {
  const t = currentTurn([u('u1', '你好'), a('a1', '你好呀'), u('u2', '天气')])
  expect(t.user?.id).toBe('u2')
  expect(t.assistant).toBeNull()
})

test('主动播报与到期留痕是旁白，不是这一轮的回答', () => {
  const t = currentTurn([
    u('u2', '打开后备箱'),
    a('a2', '要打开后备箱吗？'),
    a('x1', '💡 前方拥堵', { proactiveKind: 'scene_suggest' }),
    a('x2', '⏱ 「打开后备箱」的确认已过期，需要的话再说一次'),
  ])
  expect(t.assistant?.id).toBe('a2')
  expect(isProactive(a('x1', '💡 前方拥堵'))).toBe(true)
  expect(isAside(a('x2', '⏱ 过期'))).toBe(true)
  expect(isAside(a('a2', '要打开后备箱吗？'))).toBe(false)
})

test('空记录 / 只有助手：两边都 null', () => {
  expect(currentTurn([])).toEqual({ user: null, assistant: null })
  expect(currentTurn([a('a1', '欢迎')])).toEqual({ user: null, assistant: null })
})
