// 共享商户账号角标（CA2-17 S1，`ab576883`）：桥给麦当劳 / 瑞幸的商户卡统一打 `account_label`，
// 手机端与 HMI 同一位置（卡头标题行）原样显示；没有这个键就不画——前端不自己判断哪家是共享账号。
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(async () => true) }))

import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Text } from 'react-native'

import { McpOrder, MerchantCheckout, PaymentQr } from '@/features/cards/merchantCards'
import { paletteOf } from '@/ui/theme'

const p = paletteOf('dark', true, 'normal')

async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const texts = (view: ReactTestRenderer) => view.root.findAllByType(Text).map((t) => [t.props.children].flat().join(''))

const shared = { account: 'service', account_label: '共享商户账号' }
const cases = [
  ['merchant_checkout（订单态）', MerchantCheckout, { type: 'merchant_checkout', stage: 'order', brand: '瑞幸', order_id: 'LK1', status: 'unpaid', amount_cents: 1600 }],
  ['merchant_choices', MerchantCheckout, { type: 'merchant_choices', brand: '瑞幸', choice_kind: 'product', options: [{ label: '生椰拿铁', send_text: '点一杯生椰拿铁' }] }],
  ['mcp_order', McpOrder, { type: 'mcp_order', brand: '麦当劳', order_id: 'M1', status: 'UNPAID', amount_cents: 3900 }],
  ['payment_qr', PaymentQr, { type: 'payment_qr', payment_id: 'p1', amount: '39元', merchant: '麦当劳' }],
] as const

test.each(cases)('%s：有 account_label 就在卡头原样显示', async (_label, Comp, card) => {
  const view = await mount(createElement(Comp as never, { p, card: { ...card, ...shared }, onSend: jest.fn() }))
  try {
    expect(texts(view)).toContain('共享商户账号')
  } finally { await act(async () => { view.unmount() }) }
})

test.each(cases)('%s：没有 account_label 就不画（前端不自己判断）', async (_label, Comp, card) => {
  const view = await mount(createElement(Comp as never, { p, card, onSend: jest.fn() }))
  try {
    expect(texts(view).some((t) => t.includes('共享商户账号'))).toBe(false)
  } finally { await act(async () => { view.unmount() }) }
})
