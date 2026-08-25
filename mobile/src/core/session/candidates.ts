// 候选上下文（实施计划 M1-2）：上一轮 final 卡片里可被「第N个/换一批」消费的选项。
// 记录逻辑逐行对照 hmi/src/App.tsx:483-517（final 分支里 isLatest 才更新）；
// 消费逻辑在 sendRouter.ts。字段语义与 HMI 各 ref 一一对应。
export interface CandidateOption {
  label: string
  send_text: string
}

export interface CandidateState {
  /** 上一条 poi_list（普通）候选名：「第N个」→「导航去{名称}」（App.tsx lastPoiNamesRef） */
  poiNames: string[] | null
  /** 周边发现 place_list 候选（含高德 POI id）：「看第N个详情」透传 id 精确取详情 */
  placeItems: Array<{ id: string; name: string }> | null
  /** 充电目的地候选（dest_choice）：「第N个」回填目的地槽位，不改写为导航 */
  destChoice: string[] | null
  /** 顺路停靠候选（waypoint_choice）：「第N个」→「导航去{目的地}途经{名称}」 */
  waypointChoice: { destination: string; names: string[] } | null
  /** R4.4 澄清卡选项：「第N个」或按钮原文 → 回发 send_text（带 clarify_resume=1） */
  intentChoice: { options: CandidateOption[] } | null
  /** 商户菜单卡（product）选项：「第N个」直达该款下单句（门店选择卡由挂起补槽链自己消费） */
  merchantMenu: { options: CandidateOption[] } | null
  /** 就近类目上下文：「换一批」翻页。同关键词保页码、换类目回第 1 页 */
  category: { keyword: string; page: number } | null
}

export function emptyCandidates(): CandidateState {
  return {
    poiNames: null,
    placeItems: null,
    destChoice: null,
    waypointChoice: null,
    intentChoice: null,
    merchantMenu: null,
    category: null,
  }
}

/* eslint-disable @typescript-eslint/no-explicit-any */

/** final 到达（最新轮）时按卡片重记候选。对照 App.tsx:483-517：六个槽全部互斥清空
 *  再按卡型回填；category 只在 poi_list（普通）/place_list 分支被触碰，其余卡型保留。 */
export function recordCandidates(prev: CandidateState, card: unknown): CandidateState {
  const c = card as any
  const names: string[] | null =
    c && (c.type === 'poi_list' || c.type === 'place_list')
      ? (c.items || []).map((it: any) => it?.name).filter(Boolean)
      : null
  const next: CandidateState = {
    poiNames: null,
    placeItems: null,
    destChoice: null,
    waypointChoice: null,
    intentChoice: null,
    merchantMenu: null,
    category: prev.category,
  }
  if (!c) return next
  if (c.type === 'merchant_choices' && c.choice_kind === 'product') {
    next.merchantMenu = {
      options: (c.options || []).filter((o: any) => o?.send_text && o?.label),
    }
  }
  if (c.type === 'intent_choice') {
    next.intentChoice = { options: (c.options || []).filter((o: any) => o?.send_text) }
  } else if (c.type === 'poi_list' && c.purpose === 'dest_choice') {
    next.destChoice = names
  } else if (c.type === 'poi_list' && c.purpose === 'waypoint_choice') {
    next.waypointChoice = { destination: c.destination || '', names: names || [] }
  } else if (c.type === 'poi_list') {
    next.poiNames = names
    const kw: string = c.keyword || ''
    next.category = kw
      ? prev.category?.keyword === kw
        ? prev.category
        : { keyword: kw, page: 1 }
      : null
  } else if (c.type === 'place_list') {
    next.poiNames = names
    next.placeItems = (c.items || []).map((it: any) => ({
      id: String(it?.id || ''),
      name: it?.name,
    }))
    next.category = null
  }
  return next
}
