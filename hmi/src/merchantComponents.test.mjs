import test, { after, before } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

let vite
let CardRenderer
let ChatView
let SettingsProvider

before(async () => {
  vite = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    appType: 'custom',
    logLevel: 'silent',
    server: { middlewareMode: true },
  })
  ;({ CardRenderer } = await vite.ssrLoadModule('/src/components/Cards.tsx'))
  ;({ ChatView } = await vite.ssrLoadModule('/src/components/ChatView.tsx'))
  ;({ SettingsProvider } = await vite.ssrLoadModule('/src/settings.tsx'))
})

after(async () => {
  await vite?.close()
})

const renderCard = (card) => renderToStaticMarkup(
  React.createElement(CardRenderer, { card, onAction: () => {} }),
)

const renderConfirmation = (uiCard) => renderToStaticMarkup(
  React.createElement(SettingsProvider, null,
    React.createElement(ChatView, {
      messages: [{
        id: 'm1', role: 'assistant', text: '请确认。', needConfirm: true, uiCard,
      }],
      awaitConfirm: true,
      onConfirm: () => {},
      onQuick: () => {},
    }),
  ),
)

test('renders a link-only payment as open/copy actions without scan language', () => {
  const html = renderCard({
    type: 'payment_qr', payment_id: 'P1', amount: '23.50元',
    pay_url: 'https://pay.example/safe', qr_content: 'https://pay.example/safe',
    buttons: [{ label: '查订单', send_text: '查询瑞幸订单 L1' }],
  })
  assert.match(html, /安全支付链接/)
  assert.match(html, />打开安全支付链接</)
  assert.match(html, />复制链接</)
  assert.match(html, />查订单</)
  assert.doesNotMatch(html, /扫码/)
})

test('renders a receipt-like merchant preview and leaves confirmation to the global bubble', () => {
  const html = renderCard({
    type: 'merchant_checkout', stage: 'preview', merchant: 'luckin',
    store_name: '瑞幸·迪美店', payable_amount_cents: 2350,
    pickup_mode: '到店自取',
    items: [{ name: '生椰拿铁', quantity: 2, specs: '热 / 半糖' }],
    buttons: [
      { label: '修改订单', send_text: '修改瑞幸订单' },
      { label: '确认下单', send_text: '确认' },
    ],
  })
  for (const value of ['瑞幸', '瑞幸·迪美店', '生椰拿铁', '热 / 半糖', '23.50元', '到店自取', '修改订单']) {
    assert.match(html, new RegExp(value))
  }
  assert.doesNotMatch(html, />确认下单</)
})

test('renders the real merchant_choices items plus buttons contract', () => {
  const html = renderCard({
    type: 'merchant_choices', choice_kind: 'product', merchant: 'luckin',
    items: [{ id: 'p1', name: '生椰拿铁', subtitle: '大杯起 · 20 元' }],
    buttons: [{ label: '生椰拿铁', send_text: '选第一个生椰拿铁' }],
  })
  assert.match(html, />生椰拿铁</)
  assert.match(html, /大杯起 · 20 元/)
})

test('renders normalized order aliases and merchant-specific local actions', () => {
  const html = renderCard({
    type: 'mcp_order', server: 'mcd', orderId: 'M100', payable_cents: 1980,
    status: '待支付', store: { name: '科技园店' },
    products: [{ product_name: '麦辣鸡腿堡', quantity: 1 }],
  })
  for (const value of ['麦当劳', 'M100', '19.80元', '科技园店', '麦辣鸡腿堡', '查订单', '放弃支付']) {
    assert.match(html, new RegExp(value))
  }
  assert.doesNotMatch(html, />取消订单</)
})

test('renders a long merchant order id on its own wrapping-safe line', () => {
  const orderId = '1111222233334444555566667777'
  const html = renderCard({
    type: 'mcp_order', server: 'mcdonalds', order_id: orderId,
    amount_cents: 2650, status: '订单已取消',
  })
  assert.match(html, new RegExp(`data-order-id="${orderId}"`))
  assert.match(html, /overflow-wrap:anywhere/)
})

test('renders merchant create/cancel confirmation context without vehicle-control copy', () => {
  const create = renderConfirmation({
    type: 'merchant_checkout', confirmation_context: 'merchant_create', merchant: 'mcd',
  })
  assert.match(create, /商户下单/)
  assert.match(create, /确认下单/)
  assert.doesNotMatch(create, /已泊车|危险操作/)

  const cancel = renderConfirmation({
    type: 'merchant_checkout', confirmation_context: 'merchant_cancel', merchant: 'luckin',
  })
  assert.match(cancel, /取消订单/)
  assert.match(cancel, /可能无法恢复/)
  assert.doesNotMatch(cancel, /已泊车|危险操作/)
})

test('renders the real Luckin mcp_order cancellation fixture with only global confirmation actions', () => {
  const html = renderConfirmation({
    type: 'mcp_order',
    server: 'luckin',
    merchant: 'luckin',
    order_id: 'L100',
    status: 'cancel_pending',
    confirmation_context: 'merchant_cancel',
    buttons: [],
  })

  assert.match(html, /取消订单/)
  assert.match(html, /确认取消/)
  assert.doesNotMatch(html, /<button[^>]*>查订单<\/button>/)
  assert.doesNotMatch(html, /<button[^>]*>取消订单<\/button>/)
  assert.doesNotMatch(html, /已泊车|危险操作/)
})

test('preserves the existing parked vehicle confirmation by default', () => {
  const html = renderConfirmation(undefined)
  assert.match(html, /已泊车/)
  assert.match(html, /危险操作需二次确认/)
})

test('renders a grounded manual image with caption, page and source excerpt', () => {
  const uri = 'data:image/png;base64,eA=='
  const html = renderCard({
    type: 'manual', source_type: 'manual',
    document: { title: 'SU7用户手册', vehicle_model: 'xiaomi-su7-2024', revision: '2024-04-15' },
    images: [{
      asset_id: 'manual:p0193:i12', caption: '安全带未系提醒指示灯', page_start: 193,
      media_type: 'image/png', data_uri: uri, sha256: 'a'.repeat(64),
      width: 167, height: 168, role: 'warning_icon', match_kind: 'visual_alias',
    }],
    chunks: [{
      content: '此灯点亮表示乘员未系好座椅安全带。', page_start: 193,
      section_path: ['信息显示和娱乐', '警告灯和指示灯'],
    }],
    _prov: { mode: 'real', vendor: 'xiaomi-su7-2024-user-manual' },
  })

  assert.match(html, /<img/)
  assert.match(html, /data:image\/png;base64,eA==/)
  assert.match(html, /alt="安全带未系提醒指示灯"/)
  assert.match(html, /PDF 第 193 页/)
  assert.match(html, /未系好座椅安全带/)
})

test('manual card rejects SVG and remote image sources at render time', () => {
  const html = renderCard({
    type: 'manual',
    images: [
      { asset_id: 'svg', data_uri: 'data:image/svg+xml;base64,eA==' },
      { asset_id: 'remote', data_uri: 'https://example.com/manual.png' },
    ],
    chunks: [{ content: '仍保留文本证据。', page_start: 95 }],
  })

  assert.doesNotMatch(html, /<img/)
  assert.match(html, /仍保留文本证据/)
})
