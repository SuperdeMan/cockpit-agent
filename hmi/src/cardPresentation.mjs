// Display-only vocabulary. Capability, confirmation, and data provenance remain server owned.
export const CARD_NAMES = {
  weather: '天气实况', forecast: '天气预报', stock_quote: '行情', news_list: '新闻', news_digest: '新闻摘要',
  search_list: '搜索结果', search_answer: '搜索回答', search_result: '搜索结果', news_brief: '新闻速览', research_report: '深度调研',
  sports_scores: '赛事比分', sports_scorers: '射手榜', route_plan: '路线', charging_route: '充电规划', trip_itinerary: '行程',
  poi_list: '附近地点', poi_detail: '地点详情', place_list: '周边发现', place_detail: '商户详情',
  reminder_list: '提醒', reminder_card: '提醒', scene_card: '场景', scene_list: '场景列表', intent_choice: '请选择', manual: '车辆手册',
  vision_answer: '看一看', payment_qr: '付款码', payment_receipt: '支付回执', parking_fee: '停车费', mcp_order: '商户订单',
  mcp_result: '商户信息', merchant_checkout: '订单', merchant_choices: '商品与门店', merchant_order_preview: '订单预览',
}
export function present(value) { return value !== null && value !== undefined && value !== '' && !(typeof value === 'number' && !Number.isFinite(value)) }
export function cardTitle(card) { return CARD_NAMES[card?.type] || '结果' }
export function drivingCardSummary(card) {
  if (!card) return null
  if (card.type === 'card_group') return drivingCardSummary(card.items?.[0])
  let title = cardTitle(card), main = '', unit = '', fields = []
  switch (card.type) {
    case 'weather': title = [card.city, card.focus?.label || card.text].filter(Boolean).join(' · '); main = card.focus ? [card.focus.temp_low,card.focus.temp_high].filter(present).join('～') : card.temp; unit = '°C'; fields = [present(card.humidity) ? '湿度 '+card.humidity+'%' : '']; break
    case 'stock_quote': title = card.name || title; main = card.price; fields = [card.change_pct,card.market_time]; break
    case 'route_plan': title = card.cancelled ? '导航已取消' : card.destination || title; main = card.cancelled ? '' : card.duration_min; unit = '分钟'; fields = [present(card.distance_km) ? card.distance_km+' km' : '']; break
    case 'charging_route': title = card.destination || title; main = card.distance_km; unit = 'km'; fields = [card.soc ? '当前电量 '+card.soc : '', card.stops?.length === 0 ? '全程无需补电' : '']; break
    case 'poi_list': case 'place_list': title = card.items?.[0]?.name || title; main = card.items?.[0]?.distance_km; unit = 'km'; fields = [card.items?.[0]?.address]; break
    case 'reminder_card': title = card.title || title; main = card.time_display; fields = [card.location]; break
    case 'payment_qr': case 'payment_receipt': case 'parking_fee': title = [card.merchant, title].filter(Boolean).join(' · '); main = card.amount; fields = card.type === 'payment_qr' ? [Number(card.expires_at_ms)>0 ? new Date(Number(card.expires_at_ms)).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})+' 到期' : '', '行车中不出付款码，停车后再付'] : []; break
    default: fields = ['停车后查看详细结果']
  }
  const button = card.type === 'route_plan' && card.destination && !card.cancelled ? {label:'开始导航',text:'导航去'+card.destination}
    : card.type === 'reminder_card' && card.buttons?.[0]?.send_text ? {label:card.buttons[0].label,text:card.buttons[0].send_text} : undefined
  return { title, main: present(main) ? String(main) : '', unit: present(main) ? unit : '', fields: fields.filter(present).map(String).slice(0,2), button }
}
