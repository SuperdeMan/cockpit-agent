// mobile/test/actionSummary.test.ts
// 承诺卡 / 到期留痕的摘要源（评审 D1 / 🔁-4）：紧邻的上一条用户原话，不是助手那句通用确认句。
import type { Msg } from '@shared/types.ts'

import { SUMMARY_MAX, actionSummary } from '@/core/session/actionSummary'

const u = (id: string, text: string): Msg => ({ id, role: 'user', text })
const a = (id: string, text: string, operationId?: string): Msg => ({
  id,
  role: 'assistant',
  text,
  ...(operationId ? { needConfirm: true, operationId } : {}),
})
/** 端侧硬编码的那句（edge_call.py:272）——每个危险动作都是它 */
const GENERIC = '这项操作可能影响车辆安全，请确认是否继续。'

test('Dock 标题：取紧邻的上一条用户原话，不取带 operation_id 的助手气泡正文', () => {
  const msgs = [u('u1', '打开后备箱'), a('a1', GENERIC, 'op1')]
  expect(actionSummary(msgs, 'op1')).toBe('打开后备箱')
})

test('两条并存：两张卡的摘要逐字不同（今天 attention-two 形态两张卡逐字相同）', () => {
  const msgs = [u('u1', '打开后备箱'), a('a1', GENERIC, 'op1'), u('u2', '解锁车门'), a('a2', GENERIC, 'op2')]
  expect(actionSummary(msgs, 'op1')).toBe('打开后备箱')
  expect(actionSummary(msgs, 'op2')).toBe('解锁车门')
  expect(actionSummary(msgs, 'op1')).not.toBe(actionSummary(msgs, 'op2'))
})

test('「确认」「取消」是台账回复不是原话：跳过它们往前找', () => {
  const msgs = [u('u1', '打开后备箱'), a('a1', GENERIC, 'op1'), u('u2', '确认'), a('a2', GENERIC, 'op2')]
  expect(actionSummary(msgs, 'op2')).toBe('打开后备箱')
})

test('找不到对应助手气泡 / 前面没有用户原话 → 空串（兜底文案由调用方决定）', () => {
  expect(actionSummary([], 'op1')).toBe('')
  expect(actionSummary([a('a1', GENERIC, 'op1')], 'op1')).toBe('')
  expect(actionSummary([u('u1', '打开后备箱'), a('a1', GENERIC, 'op1')], 'op9')).toBe('')
})

test('空白归一 + 截到 SUMMARY_MAX（Dock 一行 / 留痕一句）', () => {
  const long = '帮我把  后备箱\n打开一下然后再把车窗也都关上好吗谢谢你了'
  const s = actionSummary([u('u1', long), a('a1', GENERIC, 'op1')], 'op1')
  expect(s).not.toMatch(/\s{2,}|\n/)
  expect([...s].length).toBeLessThanOrEqual(SUMMARY_MAX)
  expect(s.startsWith('帮我把 后备箱 打开')).toBe(true)
})
