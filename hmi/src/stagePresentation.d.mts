import type { Msg, UiCard } from './types'
export const STAGE_CARD_TYPES: string[]
export function flattenStageCards(card?: UiCard): UiCard[]
export function stageKind(card?: UiCard): string
export function selectStage(messages:Msg[],driving:boolean,selected?:UiCard|null):{kind:string;card?:UiCard;message?:Msg}
