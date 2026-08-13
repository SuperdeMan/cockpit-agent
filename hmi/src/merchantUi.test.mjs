import test from 'node:test'
import assert from 'node:assert/strict'

import {
  actionFor,
  confirmationPresentation,
  merchantActionButtons,
  merchantImageUrl,
  normalizeMerchantOrder,
  paymentPresentation,
  placeMenuAction,
  specChipAction,
  swapStoreAction,
} from './merchantUi.mjs'

test('builds explicit natural-language actions for merchant choices and Luckin cancellation', () => {
  assert.deepEqual(actionFor({ kind: 'cancel_luckin', orderId: 'L1' }), {
    label: '取消订单',
    send_text: '取消瑞幸订单 L1',
  })
  assert.deepEqual(actionFor({ kind: 'choose_store', merchant: '瑞幸', name: '迪美店' }), {
    label: '选择迪美店',
    send_text: '选择瑞幸门店：迪美店',
  })
})

test('keeps card actions ordinary while removing direct confirmation controls from previews', () => {
  assert.deepEqual(merchantActionButtons({
    type: 'merchant_checkout',
    stage: 'preview',
    buttons: [
      { label: '修改订单', send_text: '修改瑞幸订单' },
      { label: '确认下单', send_text: '确认' },
      { label: '确认下单', send_text: '创建瑞幸订单' },
      { label: '继续', send_text: '确认取消' },
      { label: '伪造外链', url: 'https://evil.example' },
    ],
  }), [
    { label: '修改订单', send_text: '修改瑞幸订单' },
  ])
})

test('accepts a normalized store name supplied as a plain string', () => {
  assert.equal(normalizeMerchantOrder({ merchant: 'mcd', store: '科技园店' }).storeName, '科技园店')
})

test('adds only merchant-appropriate local order actions from normalized order facts', () => {
  const luckin = merchantActionButtons({
    type: 'mcp_order', merchant: 'luckin', orderId: 'L1', status: 'UNPAID',
  })
  assert.deepEqual(luckin, [
    { label: '查订单', send_text: '查询瑞幸订单 L1' },
    { label: '取消订单', send_text: '取消瑞幸订单 L1' },
  ])

  const mcd = merchantActionButtons({
    type: 'mcp_result', server: 'mcd', order_id: 'M1', status: '待支付',
  })
  assert.deepEqual(mcd, [
    { label: '查订单', send_text: '查询麦当劳订单 M1' },
    { label: '放弃支付', send_text: '放弃支付麦当劳订单 M1' },
  ])
  assert.equal(mcd.some((button) => button.label.includes('取消')), false)
})

test('offers cancellation or abandon only for explicit unpaid states', () => {
  for (const status of ['制作中', '待取餐']) {
    assert.deepEqual(merchantActionButtons({
      type: 'mcp_order', merchant: 'luckin', order_id: 'L2', status,
    }), [
      { label: '查订单', send_text: '查询瑞幸订单 L2' },
    ])
  }
  for (const status of ['配餐中', '配送中']) {
    assert.deepEqual(merchantActionButtons({
      type: 'mcp_order', merchant: 'mcd', order_id: 'M2', status,
    }), [
      { label: '查订单', send_text: '查询麦当劳订单 M2' },
    ])
  }
})

test('does not synthesize order actions while a merchant confirmation is pending', () => {
  for (const card of [
    {
      type: 'mcp_order', merchant: 'luckin', order_id: 'L1', status: 'created',
      confirmation_context: 'merchant_create', buttons: [],
    },
    {
      type: 'mcp_order', merchant: 'luckin', order_id: 'L1', status: 'cancel_pending',
      confirmation_context: 'merchant_cancel', buttons: [],
    },
    {
      type: 'mcp_order', merchant: 'luckin', order_id: 'L1', status: 'cancel_pending',
      buttons: [],
    },
  ]) {
    assert.deepEqual(merchantActionButtons(card), [])
  }
})

test('offers a deterministic swap-store chip while a create confirmation is pending', () => {
  // demo-mkemhn 2fd09d52：用户裸说「我想要换一个」时 planner 判不动、chitchat 编造
  // 新确认。换店做成按钮后，发出的句子带品牌与商品（与范例「换一家X门店，还是点Y」
  // 句式闭环），换店重规划时商品不丢。
  const preview = {
    type: 'merchant_order_preview', merchant: '瑞幸',
    confirmation_context: 'merchant_create', store_name: '什刹海新地佰店',
    items: [{ name: '埃塞瑰夏冷萃', quantity: 1, specifications: ['大杯', '冰'] }],
    amount_cents: 1090, buttons: [],
  }
  assert.deepEqual(swapStoreAction(preview), {
    label: '换一家门店', send_text: '换一家瑞幸门店，还是点埃塞瑰夏冷萃',
  })
  assert.deepEqual(merchantActionButtons(preview), [
    { label: '换一家门店', send_text: '换一家瑞幸门店，还是点埃塞瑰夏冷萃' },
  ])

  const mcd = swapStoreAction({
    type: 'merchant_order_preview', merchant: 'mcd',
    confirmation_context: 'merchant_create',
    items: [{ name: '蘸酱麦辣大四角', quantity: 1 }], buttons: [],
  })
  assert.deepEqual(mcd, {
    label: '换一家门店', send_text: '换一家麦当劳门店，还是点蘸酱麦辣大四角',
  })
})

test('never offers swap-store on cancel confirmations or unknown merchants', () => {
  assert.equal(swapStoreAction({
    type: 'merchant_order_preview', merchant: '瑞幸',
    confirmation_context: 'merchant_cancel',
    items: [{ name: '埃塞瑰夏冷萃', quantity: 1 }],
  }), null)
  assert.equal(swapStoreAction({
    type: 'merchant_order_preview', merchant: 'demo-coffee',
    confirmation_context: 'merchant_create',
    items: [{ name: '拿铁', quantity: 1 }],
  }), null)
  assert.equal(swapStoreAction({
    type: 'merchant_order_preview', merchant: '瑞幸',
    confirmation_context: 'merchant_create', items: [],
  }), null)
})

test('spec chips send the deterministic reorder sentence and skip the current choice', () => {
  // demo-3ukshz #3：预览卡规格 chip 点非当前项 → 「在{店}点一杯{品}，要{规格}」
  // 与范例句式闭环；当前选中项没有可改的余地，不出动作。
  const preview = {
    type: 'merchant_order_preview', merchant: '瑞幸',
    confirmation_context: 'merchant_create', store_name: '科技园文化广场店',
    items: [{ name: '埃塞瑰夏冷萃', quantity: 1, specifications: ['大杯', '冰'] }],
  }
  const group = { name: '温度', selected: '冰', options: [{ label: '冰' }, { label: '热' }] }
  assert.deepEqual(specChipAction(preview, group, '热'), {
    label: '热', send_text: '在科技园文化广场店点一杯埃塞瑰夏冷萃，要热',
  })
  assert.equal(specChipAction(preview, group, '冰'), null)   // 当前项不出动作
  assert.equal(specChipAction({ ...preview, store_name: '' }, group, '热'), null)

  // 麦当劳量词用「份」；数量 >1 原样带上
  const mcd = {
    type: 'merchant_order_preview', merchant: 'mcd',
    confirmation_context: 'merchant_create', store_name: '人民广场麦当劳餐厅',
    items: [{ name: '巨无霸套餐', quantity: 2 }],
  }
  assert.deepEqual(
    specChipAction(mcd, { name: '口味', selected: '原味' }, '辣味'),
    { label: '辣味', send_text: '在人民广场麦当劳餐厅点2份巨无霸套餐，要辣味' })
})

test('place rows expose brand menu shortcuts aligned with the exemplar sentence shapes', () => {
  assert.deepEqual(placeMenuAction('瑞幸咖啡(深铁金融科技大厦店)'), {
    label: '看菜单',
    send_text: '瑞幸咖啡(深铁金融科技大厦店)这家瑞幸咖啡，我要看看菜单',
  })
  assert.deepEqual(placeMenuAction('麦当劳(科苑南路餐厅)'), {
    label: '看菜单', send_text: '看看麦当劳(科苑南路餐厅)的菜单',
  })
  assert.equal(placeMenuAction('老王川菜馆'), null)
  assert.equal(placeMenuAction(''), null)
})

test('normalizes snake/camel merchant order fields without exposing checkout tokens', () => {
  assert.deepEqual(normalizeMerchantOrder({
    merchant: 'lucky-order',
    orderId: 'L2',
    payable_amount_cents: 2350,
    store: { name: '万象天地店' },
    products: [{ product_name: '生椰拿铁', quantity: 2, additionDesc: '热 / 半糖' }],
    checkout_token: 'must-not-render',
  }), {
    brand: '瑞幸',
    orderId: 'L2',
    amount: '23.50元',
    status: '',
    storeName: '万象天地店',
    fulfillment: '',
    items: [{ name: '生椰拿铁', quantity: 2, specs: '热 / 半糖' }],
  })
})

test('normalizes the plural specifications field used by real merchant previews', () => {
  assert.deepEqual(normalizeMerchantOrder({
    items: [{ name: '生椰拿铁', quantity: 1, specifications: ['大杯', '热', '少甜'] }],
  }).items, [
    { name: '生椰拿铁', quantity: 1, specs: '大杯 / 热 / 少甜' },
  ])
})

test('uses scan copy only when an SVG QR is actually present', () => {
  const linkOnly = paymentPresentation({
    pay_url: 'https://pay.example/safe',
    qr_content: 'https://pay.example/safe',
  })
  assert.equal(linkOnly.hasQr, false)
  assert.equal(linkOnly.title, '安全支付链接')
  assert.equal(linkOnly.hint, '打开或复制安全支付链接完成支付')
  assert.equal(linkOnly.safeUrl, 'https://pay.example/safe')
  assert.doesNotMatch(`${linkOnly.title}${linkOnly.hint}`, /扫码/)

  const qr = paymentPresentation({
    qr_svg: 'data:image/svg+xml;base64,PHN2Zy8+',
    pay_url: 'https://pay.example/safe',
  })
  assert.equal(qr.hasQr, true)
  assert.equal(qr.title, '扫码支付')
  assert.match(qr.hint, /扫码/)
})

test('fails closed on non-HTTPS local payment actions', () => {
  assert.equal(paymentPresentation({ pay_url: 'javascript:alert(1)' }).safeUrl, '')
  assert.equal(paymentPresentation({ pay_url: 'http://pay.example' }).safeUrl, '')
})

test('shows merchant-specific confirmation copy from confirmation_context', () => {
  assert.deepEqual(confirmationPresentation('merchant_create'), {
    kind: 'merchant_create',
    label: '商户下单',
    detail: '确认订单信息后创建未支付订单，完成扫码或打开安全支付链接后才会付款。',
    confirmLabel: '确认下单',
  })
  assert.deepEqual(confirmationPresentation('merchant_cancel'), {
    kind: 'merchant_cancel',
    label: '取消订单',
    detail: '取消后可能无法恢复，请确认。',
    confirmLabel: '确认取消',
  })
  assert.equal(
    confirmationPresentation('merchant_order', 'merchant_checkout').kind,
    'merchant_create',
  )
})

test('preserves the existing vehicle-control confirmation copy by default', () => {
  assert.deepEqual(confirmationPresentation(''), {
    kind: 'vehicle',
    label: '已泊车',
    detail: '危险操作需二次确认',
    confirmLabel: '确认',
  })
})

test('merchant product images render only from credential-free https urls', () => {
  // 桥侧已过 servers.yaml::image_hosts 精确白名单；这里是渲染端的第二道
  // ——卡片数据到了前端仍然只是一段别人给的 JSON，而它下一步会变成一次网络请求。
  assert.equal(
    merchantImageUrl('https://img04.luckincoffeecdn.com/pic/1262.png'),
    'https://img04.luckincoffeecdn.com/pic/1262.png',
  )
  for (const bad of [
    'http://img04.luckincoffeecdn.com/pic/1.png',          // 明文
    'https://user:pw@img04.luckincoffeecdn.com/1.png',     // 带凭据
    'javascript:alert(1)',                                  // 伪协议
    'data:image/png;base64,AAAA',                           // 内联数据
    '/pic/1.png', '', undefined, null, 42, {},
  ]) {
    assert.equal(merchantImageUrl(bad), '', String(bad))
  }
})
