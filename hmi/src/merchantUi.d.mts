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
// 下面三条 2026-08-27 补：`.mjs` 一直导出它们，`.d.mts` 漏了。
// hmi 走 vite（esbuild 不做类型检查）且 package.json 没有 typecheck 脚本，
// 所以这缺口从没红过；mobile 跑 tsc 才撞上——与 M2 的 ttsQueue.d.mts 同一形态。
// **只补声明，实现一行未改。**
export function swapStoreAction(card?: unknown): MerchantActionButton | null
export function specChipAction(
  card?: unknown,
  group?: unknown,
  optionLabel?: unknown,
): MerchantActionButton | null
export function placeMenuAction(name?: unknown): MerchantActionButton | null
export function paymentPresentation(card?: unknown): PaymentPresentation
export function confirmationPresentation(context?: unknown, cardType?: string): ConfirmationPresentation
export function merchantImageUrl(value?: unknown): string
