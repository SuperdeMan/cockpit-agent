export const STAGE_CARD_TYPES = ['weather','forecast','poi_list','poi_detail','place_list','place_detail','route_plan','charging_route','trip_itinerary','reminder_list','reminder_card','manual','research_report','news_brief','news_list','news_digest','search_answer','search_result','stock_quote','payment_qr']
export function flattenStageCards(card) { return !card ? [] : card.type === 'card_group' ? (card.items || []).flatMap(flattenStageCards) : [card] }
export function stageKind(card) {
  if (!card) return 'idle'
  if (['weather','forecast'].includes(card.type)) return 'weather'
  if (['poi_list','poi_detail','place_list','place_detail','route_plan','charging_route','trip_itinerary'].includes(card.type)) return 'map'
  if (['reminder_list','reminder_card'].includes(card.type)) return 'agenda'
  if (card.type === 'payment_qr') return 'payment'
  return STAGE_CARD_TYPES.includes(card.type) ? 'reading' : 'idle'
}
export function selectStage(messages, driving, selected) {
  if (!driving && selected && STAGE_CARD_TYPES.includes(selected.type)) return {kind:stageKind(selected),card:selected}
  for (const message of [...messages].reverse()) {
    for (const card of flattenStageCards(message.uiCard)) {
      if (driving) {
        if (card.type === 'route_plan' && card.cancelled) return {kind:'idle'}
        if (['route_plan','charging_route'].includes(card.type)) return {kind:'map',card}
      } else if (STAGE_CARD_TYPES.includes(card.type)) return {kind:stageKind(card),card}
    }
    if (!driving && message.actions?.some(a=>a.type?.startsWith('vehicle.control'))) return {kind:'vehicle'}
    if (!driving && message.actions?.some(a=>a.type?.startsWith('media.control'))) return {kind:'media',message}
  }
  return {kind:'idle'}
}
