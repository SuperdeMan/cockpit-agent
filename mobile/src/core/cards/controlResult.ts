// Shared declaration lives in hmi/src; both clients use the same result evidence projection.
export { STATUS_WORD, controlStatusOf, controlItems, overallStatus, itemName } from '@shared/controlResult.mjs'
export type { ControlStatus, ControlItem } from '@shared/controlResult.mjs'
