import type { McpOrderCard, PaymentQrCard } from './types'

// 真实商户支付卡会在支付字段之外携带订单归属信息。
export const merchantPaymentCard: PaymentQrCard = {
  type: 'payment_qr',
  payment_id: 'P100',
  amount: '20.00元',
  merchant: 'luckin',
  order_id: 'L100',
  status: 'UNPAID',
  store_name: '人民广场店',
}

// 瑞幸取消写操作的真实 NEED_CONFIRM 卡就是 mcp_order，而非 merchant_checkout。
export const merchantCancelCard: McpOrderCard = {
  type: 'mcp_order',
  server: 'luckin',
  merchant: 'luckin',
  order_id: 'L100',
  status: 'cancel_pending',
  confirmation_context: 'merchant_cancel',
  buttons: [],
}
