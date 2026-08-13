const text = (value) => typeof value === 'string' ? value.trim() : ''

const merchantSource = (card = {}) => [
  card.brand,
  card.merchant,
  card.server,
  card.merchant_name,
].map(text).filter(Boolean).join(' ')

export function merchantBrand(card = {}) {
  const source = merchantSource(card)
  if (/瑞幸|luck/i.test(source)) return '瑞幸'
  if (/麦当劳|mcd|golden.?arches/i.test(source)) return '麦当劳'
  return text(card.brand) || text(card.merchant_name) || text(card.merchant) || '商户'
}

export function actionFor(action = {}) {
  const orderId = text(action.orderId)
  if (action.kind === 'cancel_luckin' && orderId) {
    return { label: '取消订单', send_text: `取消瑞幸订单 ${orderId}` }
  }
  if (action.kind === 'query_order' && orderId) {
    const merchant = text(action.merchant) || '商户'
    return { label: '查订单', send_text: `查询${merchant}订单 ${orderId}` }
  }
  if (action.kind === 'abandon_mcd' && orderId) {
    return { label: '放弃支付', send_text: `放弃支付麦当劳订单 ${orderId}` }
  }
  if (action.kind === 'choose_store') {
    const merchant = text(action.merchant)
    const name = text(action.name)
    if (merchant && name) {
      return { label: `选择${name}`, send_text: `选择${merchant}门店：${name}` }
    }
  }
  return null
}

const centsText = (value) => {
  const amount = Number(value)
  if (!Number.isInteger(amount) || amount < 0) return ''
  return `${Math.floor(amount / 100)}.${String(amount % 100).padStart(2, '0')}元`
}

const itemView = (item) => {
  if (!item || typeof item !== 'object') return null
  const name = text(item.name) || text(item.product_name) || text(item.item_name) || text(item.title)
  if (!name) return null
  const rawQuantity = Number(item.quantity ?? item.qty ?? 1)
  const quantity = Number.isInteger(rawQuantity) && rawQuantity > 0 ? rawQuantity : 1
  const rawSpecs = item.specs ?? item.specifications ?? item.specification
    ?? item.additionDesc ?? item.size ?? ''
  const specs = Array.isArray(rawSpecs)
    ? rawSpecs.map(text).filter(Boolean).join(' / ')
    : text(rawSpecs)
  return { name, quantity, specs }
}

export function normalizeMerchantOrder(card = {}) {
  const store = card.store && typeof card.store === 'object' ? card.store : null
  const rawItems = Array.isArray(card.items)
    ? card.items
    : Array.isArray(card.products)
      ? card.products
      : card.item_name || card.product_name
        ? [{
            name: card.item_name || card.product_name,
            quantity: card.quantity,
            specs: card.specs || card.specification || card.additionDesc,
          }]
        : []
  const explicitAmount = text(card.amount) || text(card.payable_amount) || text(card.payable)
  const cents = card.amount_cents ?? card.payable_amount_cents ?? card.payable_cents ?? card.amountCents
  return {
    brand: merchantBrand(card),
    orderId: text(card.order_id) || text(card.orderId) || text(card.merchant_order_id),
    amount: explicitAmount || centsText(cents),
    status: text(card.status) || text(card.order_status),
    storeName: text(card.store_name) || text(card.storeName) || text(card.store)
      || text(store?.name) || text(store?.storeName),
    fulfillment: text(card.fulfillment) || text(card.pickup_mode) || text(card.take_way) || text(card.takeWay),
    items: rawItems.map(itemView).filter(Boolean),
  }
}

const directConfirmation = (button) => {
  const label = text(button.label).replace(/\s+/g, '')
  const sendText = text(button.send_text).replace(/\s+/g, '')
  return /^(?:确认(?:下单|创建(?:订单)?|取消(?:订单)?)?|立即下单|创建订单)$/.test(label)
    || /^确认(?:$|下单|创建(?:订单)?|取消(?:订单)?)/.test(sendText)
}

const declaredButtons = (card) => {
  const buttons = Array.isArray(card?.buttons) ? card.buttons : []
  const options = Array.isArray(card?.options) ? card.options : []
  const normalized = []
  for (const candidate of [...buttons, ...options]) {
    if (!candidate || typeof candidate !== 'object') continue
    const label = text(candidate.label) || text(candidate.name)
    const sendText = text(candidate.send_text)
    if (!label || !sendText) continue
    const button = { label, send_text: sendText }
    if (!directConfirmation(button)) normalized.push(button)
  }
  return normalized
}

const terminalStatus = (status) => (
  /(?:^|[^A-Z])(?:PAID|COMPLETED|CANCELLED|CANCELED|REFUNDED)(?:$|[^A-Z])/i.test(status)
  || /已支付|已完成|已取消|已退款/.test(status)
)

const unpaidStatus = (status) => {
  const normalized = text(status).toLowerCase().replace(/[.\s-]+/g, '_')
  return /^(?:unpaid|pending_payment|awaiting_payment|created|待支付|待付款|未支付|已创建)$/.test(normalized)
}

export function swapStoreAction(card = {}) {
  // 订单预览期的「换一家门店」确定性补全（demo-mkemhn 2fd09d52）：用户裸说
  // 「我想要换一个」时 planner 判不动、chitchat 又会编造新确认——把这个高频诉求
  // 做成按钮，发出的句子带品牌与商品（与范例库「换一家X门店，还是点Y」句式闭环），
  // 换店重规划时商品不丢。仅创建确认（merchant_create）出现；取消确认换店无意义。
  const presentation = confirmationPresentation(
    card.confirmation_context, text(card.type))
  if (presentation.kind !== 'merchant_create') return null
  const order = normalizeMerchantOrder(card)
  if (order.brand !== '瑞幸' && order.brand !== '麦当劳') return null
  const item = text(order.items[0]?.name)
  if (!item) return null
  return { label: '换一家门店', send_text: `换一家${order.brand}门店，还是点${item}` }
}

export function specChipAction(card = {}, group = {}, optionLabel) {
  // 预览卡规格 chip（demo-3ukshz #3）：点非当前项发确定句式、重出预览。
  // 句式与范例「在{店}点一{量词}{品}，要{规格}」闭环——规格词由 planner 按
  // _SPEC_GROUPS 落对应槽（温度/冰量/糖度/奶底），真正的规格落定仍走官方
  // switchProduct 链。当前选中项不出动作（没有可改的余地）。
  const order = normalizeMerchantOrder(card)
  const store = text(order.storeName)
  const item = text(order.items[0]?.name)
  const label = text(optionLabel)
  if (!store || !item || !label || label === text(group && group.selected)) return null
  const unit = order.brand === '麦当劳' ? '份' : '杯'
  const q = Number(order.items[0]?.quantity)
  const count = Number.isInteger(q) && q > 1 ? String(q) : '一'
  return { label, send_text: `在${store}点${count}${unit}${item}，要${label}` }
}

export function placeMenuAction(name) {
  // 周边发现列表里的品牌门店补「看菜单」直达（发现→看单→点单三步全可点按）。
  // 句式与范例库对齐：瑞幸走「X这家瑞幸咖啡，我要看看菜单」（planner 按范例
  // nearby.search(keyword=瑞幸咖啡 X) 重建可信链）；麦当劳走「看看X的菜单」
  // （store_hint 文本直达）。非两品牌不出按钮——没有对应的菜单能力，按钮就是谎言。
  const n = text(name)
  if (!n) return null
  if (/瑞幸|luck/i.test(n)) {
    return { label: '看菜单', send_text: `${n}这家瑞幸咖啡，我要看看菜单` }
  }
  if (/麦当劳|mcd/i.test(n)) {
    return { label: '看菜单', send_text: `看看${n}的菜单` }
  }
  return null
}

export function merchantActionButtons(card = {}) {
  const result = declaredButtons(card)
  const order = normalizeMerchantOrder(card)
  const confirmationPending = Boolean(text(card.confirmation_context))
  const normalizedStatus = text(order.status).toLowerCase().replace(/[.\s-]+/g, '_')
  if (confirmationPending || normalizedStatus === 'cancel_pending'
      || !order.orderId || terminalStatus(order.status)) {
    // 换店 chip 只在**确认挂起中**出现（订单还没创建才谈得上换）；已创建/终态的
    // 预览卡不出——那时换店=取消+重下，是另一条流程。
    const swap = confirmationPending ? swapStoreAction(card) : null
    if (swap && !result.some((existing) => existing.label === swap.label)) {
      result.push(swap)
    }
    return result
  }

  const additions = [actionFor({ kind: 'query_order', merchant: order.brand, orderId: order.orderId })]
  if (unpaidStatus(order.status) && order.brand === '瑞幸') {
    additions.push(actionFor({ kind: 'cancel_luckin', orderId: order.orderId }))
  }
  if (unpaidStatus(order.status) && order.brand === '麦当劳') {
    additions.push(actionFor({ kind: 'abandon_mcd', orderId: order.orderId }))
  }
  for (const button of additions.filter(Boolean)) {
    if (!result.some((existing) => existing.label === button.label || existing.send_text === button.send_text)) {
      result.push(button)
    }
  }
  return result
}

const safeHttpsUrl = (value) => {
  const candidate = text(value)
  if (!candidate) return ''
  try {
    const parsed = new URL(candidate)
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return ''
    return parsed.href
  } catch {
    return ''
  }
}

export function merchantImageUrl(value) {
  // 商品图在桥侧已过 image_hosts 精确白名单；渲染端再挡一次协议与凭据是纵深防御——
  // 卡片数据到了这里仍然只是「一段别人给的 JSON」，而它下一步就会变成一次网络请求。
  return safeHttpsUrl(value)
}

export function paymentPresentation(card = {}) {
  const qrSvg = text(card.qr_svg)
  const hasQr = qrSvg.startsWith('data:image/svg+xml')
  const safeUrl = safeHttpsUrl(card.pay_url) || safeHttpsUrl(card.qr_content)
  if (hasQr) {
    return {
      hasQr: true,
      title: '扫码支付',
      hint: '请用手机扫码完成支付',
      safeUrl,
    }
  }
  return {
    hasQr: false,
    title: '安全支付链接',
    hint: safeUrl
      ? '打开或复制安全支付链接完成支付'
      : '支付入口暂不可用，请到商家应用内完成支付',
    safeUrl,
  }
}

export function confirmationPresentation(context = '', cardType = '') {
  const normalized = text(context).toLowerCase().replace(/[.\s-]+/g, '_')
  if (/(?:^|_)merchant_(?:order_)?cancel(?:$|_)/.test(normalized)
      || /(?:^|_)cancel_(?:merchant_)?order(?:$|_)/.test(normalized)) {
    return {
      kind: 'merchant_cancel',
      label: '取消订单',
      detail: '取消后可能无法恢复，请确认。',
      confirmLabel: '确认取消',
    }
  }
  const createContext = /(?:^|_)merchant_(?:order(?:_(?:create|checkout))?|create|checkout)(?:$|_)/.test(normalized)
    || /(?:^|_)create_(?:merchant_)?order(?:$|_)/.test(normalized)
    || cardType === 'merchant_order_preview'
  if (createContext) {
    return {
      kind: 'merchant_create',
      label: '商户下单',
      detail: '确认订单信息后创建未支付订单，完成扫码或打开安全支付链接后才会付款。',
      confirmLabel: '确认下单',
    }
  }
  return {
    kind: 'vehicle',
    label: '已泊车',
    detail: '危险操作需二次确认',
    confirmLabel: '确认',
  }
}
