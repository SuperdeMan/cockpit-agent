export type MerchantActionButton = { label: string; send_text: string }

export type MerchantOrderView = {
  brand: string
  orderId: string
  amount: string
  status: string
  storeName: string
  fulfillment: string
  items: Array<{ name: string; quantity: number; specs: string }>
}

export type PaymentPresentation = {
  hasQr: boolean
  title: string
  hint: string
  safeUrl: string
}

export type ConfirmationPresentation = {
  kind: 'merchant_create' | 'merchant_cancel' | 'vehicle'
  label: string
  detail: string
  confirmLabel: string
}

export function merchantBrand(card?: unknown): string
export function actionFor(action?: unknown): MerchantActionButton | null
export function normalizeMerchantOrder(card?: unknown): MerchantOrderView
export function merchantActionButtons(card?: unknown): MerchantActionButton[]
export function paymentPresentation(card?: unknown): PaymentPresentation
export function confirmationPresentation(context?: unknown, cardType?: string): ConfirmationPresentation
