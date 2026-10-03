// mobile/src/core/cards/drivingSummary.ts
// 行车摘要模板（v3 P4d，差异 D11；Figma 06 各卡型的「行车摘要」格 + 组件 Card/DrivingSummary）：
// 一屏一卡，只给「类别 · 实体」标题 + 一行主数值 + 一行 ≤2 个字段 + 至多 1 个主按钮。
// 修前从任意卡里探 PRIMARY_KEYS、拿 `main.type`（weather / route_plan）当标题——机器名直接上屏。
//
// 主按钮只取全量卡上已有的动作，句子与全量卡同一个函数（cardActions.ts）；全量卡没有的动作这里不造
// （充电路线全量卡没有「开始导航」，行车态也不出）。卡上自带的按钮（商户 / 提醒 / 兜底卡）取第一个。
// 扫码支付在行车时不出二维码，只给金额与到期时刻。车控结果卡不是 uiCard，由 ControlResult 自己出行车形态。
// 零 RN import（图标名是类型导入）。
import type { CardButton } from '@shared/types.ts'
import { merchantActionButtons, normalizeMerchantOrder, paymentPresentation } from '@shared/merchantUi.mjs'

import type { IconName } from '../../ui/Icon'
import { navToText, poiPickText, sceneOpenText, tripStopText } from './cardActions'
import { cardPrimaryButton, cardPrimaryFields, fieldLabel } from './cardFields'
import { splitCardGroup } from './cardGroup'
import {
  CONF_LABEL, REMINDER_TITLE, SCENE_STATE, clockLabel, confLevel, dayLabel, durationLabel, etaClock,
  isPlaceholderOrderId, kickoffLabel, orderStatusLabel, skyIcon, weatherIcon,
} from './cardMeta'

export interface DrivingSummary {
  icon: IconName
  title: string
  /** 主数值（Figma numeric/l）：一行 */
  main: string
  /** ≤2 个字段，「 · 」相连；没有就空串 */
  sub: string
  /** 主数值的语义色：涨 / 跌（行情卡的数据色）、warn（车控没生效 / 未核实，琥珀）；缺省主色 */
  tone?: 'up' | 'down' | 'warn'
  /** 副行要提醒人留意（预警 / 进行中 / 已过期）：琥珀色 */
  subWarn?: boolean
  button: { label: string; send_text: string } | null
}

type AnyCard = Record<string, any>

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : typeof v === 'number' && Number.isFinite(v) ? String(v) : '')
/** 「 · 」连接非空字段，最多 n 个（计划：≤2 字段） */
const join = (parts: unknown[], n = 2): string => parts.map(str).filter(Boolean).slice(0, n).join(' · ')
/** 第一句（行车只念第一句）：按句末标点切 */
const firstSentence = (text: string): string => str(text).split(/[。！？\n]/)[0]?.trim() ?? ''
const firstButton = (list: unknown): DrivingSummary['button'] => {
  const b = (Array.isArray(list) ? list : []).find((x: CardButton | undefined) => x?.label && x?.send_text) as CardButton | undefined
  return b ? { label: String(b.label), send_text: String(b.send_text) } : null
}
const more = (n: number, unit: string): string => (n > 0 ? `另 ${n} ${unit}` : '')

function weather(c: AnyCard): DrivingSummary {
  const f = c.focus
  const alert = str(c.alerts?.[0]?.title)
  const aqi = str(c.air_quality?.aqi)
  return {
    icon: weatherIcon(c as never),
    title: `天气 · ${str(c.city)}`,
    main: f ? `${str(f.label)} ${str(f.text_day)} ${str(f.temp_low)}–${str(f.temp_high)}°` : `${str(c.temp)}° ${str(c.text)}`,
    // 焦点日（问明天）给今天实况作对照；今天给预警与空气质量（预警优先）
    sub: f ? join([alert, `今天 ${str(c.temp)}° ${str(c.text)}`]) : join([alert, aqi ? `AQI ${aqi} ${str(c.air_quality?.category)}`.trim() : '']),
    subWarn: !!alert,
    button: null,
  }
}

function forecast(c: AnyCard, now: number): DrivingSummary {
  const days: AnyCard[] = Array.isArray(c.days) ? c.days : []
  const label = (d: AnyCard) => dayLabel(str(d.date), now)
  const range = (d: AnyCard) => `${str(d.temp_low)}–${str(d.temp_high)}°`
  // 主数值给下一个白天（预报通常问的是之后几天）；第一天不是今天就用第一天
  const head = days.find((d) => label(d) !== '今天') ?? days[0]
  const today = days.find((d) => label(d) === '今天')
  const severe = days.find((d) => d !== head && /雷|雪|暴/.test(str(d.text_day)))
  return {
    icon: skyIcon(str(head?.text_day)),
    title: `未来预报 · ${str(c.city)}`,
    main: head ? `${label(head)} ${str(head.text_day)} ${range(head)}` : '暂无预报',
    sub: join([today && today !== head ? `今天 ${str(today.text_day)} ${range(today)}` : '', severe ? `${label(severe)}有${str(severe.text_day)}` : '']),
    button: null,
  }
}

function stock(c: AnyCard): DrivingSummary {
  const change = str(c.change)
  const flat = /^[+-]?0+(\.0+)?$/.test(change)
  return {
    icon: 'building',
    title: `${str(c.name)} · ${str(c.market) || str(c.symbol)}`,
    main: `${str(c.price)}  ${str(c.change_pct)}`.trim(),
    sub: join([change, c.market_time ? `行情 ${clockLabel(c.market_time) || str(c.market_time)}` : '']),
    tone: flat || !change ? undefined : change.startsWith('-') ? 'down' : 'up',
    button: null,
  }
}

function newsLike(icon: IconName, title: string, items: AnyCard[], timeKey: string): DrivingSummary {
  const top = items[0]
  return {
    icon,
    title,
    main: str(top?.title) || '暂无要闻',
    sub: join([top?.source, top?.[timeKey] ? clockLabel(top[timeKey]) || str(top[timeKey]) : '', more(items.length - 1, '条')]),
    button: null,
  }
}

function searchResult(c: AnyCard): DrivingSummary {
  const s: AnyCard[] = Array.isArray(c.sources) ? c.sources : []
  return {
    icon: 'search',
    title: `检索 · ${str(c.query)}`,
    main: str(s[0]?.title) || '没有找到来源',
    sub: join([s[0]?.source, s[0]?.published ? clockLabel(s[0].published) || str(s[0].published) : '', more(s.length - 1, '条')]),
    button: null,
  }
}

function research(c: AnyCard): DrivingSummary {
  const conf = confLevel(c.overall_confidence)
  const gaps = Array.isArray(c.gaps) ? c.gaps.length : 0
  return {
    icon: 'research',
    title: `调研 · ${str(c.question)}`,
    main: firstSentence(c.summary) || str(c.sections?.[0]?.heading) || '报告已生成',
    sub: join([conf ? CONF_LABEL[conf] : '', gaps ? `${gaps} 处没覆盖` : '']),
    button: null,
  }
}

function sportsScores(c: AnyCard, now: number): DrivingSummary {
  const list: AnyCard[] = Array.isArray(c.fixtures) ? c.fixtures : []
  const f = list[0]
  const scored = f && (f.status === 'live' || f.status === 'finished') && (str(f.home_goals) || str(f.away_goals))
  const main = !f
    ? '暂无比赛安排'
    : scored
      ? `${str(f.home)} ${str(f.home_goals) || '0'} : ${str(f.away_goals) || '0'} ${str(f.away)}`
      : `${str(f.home)} vs ${str(f.away)}`
  const status = !f ? '' : f.status === 'scheduled' ? kickoffLabel(f.kickoff, now) || str(f.status_text) : str(f.status_text)
  return {
    icon: 'football',
    title: str(c.title) || '比分',
    main,
    sub: join([status, more(list.length - 1, '场')]),
    subWarn: f?.status === 'live',
    button: null,
  }
}

function sportsScorers(c: AnyCard): DrivingSummary {
  const list: AnyCard[] = Array.isArray(c.scorers) ? c.scorers : []
  const [a, b] = list
  return {
    icon: 'trophy',
    title: str(c.title) || '射手榜',
    main: a ? `${str(a.player)} ${str(a.goals)} 球` : '暂无射手榜数据',
    sub: b ? `第 ${str(b.rank) || 2} · ${str(b.player)} ${str(b.goals)} 球` : '',
    button: null,
  }
}

function routePlan(c: AnyCard): DrivingSummary {
  const dest = str(c.destination)
  if (c.cancelled) return { icon: 'route-map', title: '导航已结束', main: dest, sub: '', button: null }
  const dur = durationLabel(c.duration_min)
  const eta = etaClock(c.eta_ts)
  return {
    icon: 'route-map',
    title: `${c.estimate ? '路线测算' : '路线'} · ${dest}`,
    main: dur || dest,
    sub: join([c.distance_km !== undefined ? `${str(c.distance_km)} km` : '', eta ? `预计 ${eta} 到` : '']),
    // 全量卡只在「只算不导」时给「开始导航」（已在导航时没有这个动作）
    button: c.estimate ? { label: '开始导航', send_text: navToText(dest) } : null,
  }
}

function chargingRoute(c: AnyCard): DrivingSummary {
  const stops: AnyCard[] = Array.isArray(c.stops) ? c.stops : []
  const next = stops[0]
  return {
    icon: 'charging-station',
    title: `充电路线 · ${str(c.destination)}`,
    main: stops.length ? `补电 ${stops.length} 次` : '全程无需补电',
    sub: next
      ? join([next.at_km !== undefined ? `下一站 约 ${str(next.at_km)} km` : '下一站', next.name])
      : join([c.distance_km !== undefined ? `${str(c.distance_km)} km` : '', durationLabel(c.duration_min)]),
    button: null,
  }
}

function trip(c: AnyCard): DrivingSummary {
  const day: AnyCard | undefined = Array.isArray(c.itinerary) ? c.itinerary[0] : undefined
  const stops: AnyCard[] = Array.isArray(day?.stops) ? day.stops : []
  const first = stops[0]
  // 全量卡只给已接地的站点导航入口
  const nav = stops.find((s) => s?.grounded && str(s.name))
  const w = day?.weather
  return {
    icon: 'itinerary',
    title: `行程 · ${str(c.destination)} ${str(c.days)} 日`,
    // 「第一站」不是「下一站」：系统不知道走到哪了
    main: first ? `第一站 ${str(first.name)}` : '行程待补全',
    sub: join([day ? `D${str(day.day_index)} ${stops.length} 个点` : '', w ? `${str(w.text)} ${str(w.temp_low)}–${str(w.temp_high)}°`.trim() : '']),
    button: nav && day ? { label: `导航去${str(nav.name)}`, send_text: tripStopText(Number(day.day_index), str(nav.name)) } : null,
  }
}

function poiList(c: AnyCard): DrivingSummary {
  const items: AnyCard[] = Array.isArray(c.items) ? c.items : []
  const top = items[0]
  const purpose = c.purpose
  const title = str(c.title) || (purpose === 'dest_choice' ? '选择充电目的地' : purpose === 'waypoint_choice' ? `顺路停靠 · 去${str(c.destination)}` : `候选 · ${str(c.keyword) || '地点'}`)
  const pick = purpose === 'dest_choice' ? '选第 1 个' : purpose === 'waypoint_choice' ? '途经第 1 个' : '导航去第 1 个'
  return {
    icon: purpose === 'dest_choice' ? 'flag' : 'location',
    title,
    main: str(top?.name) || '没有候选',
    sub: join([top?.distance_km !== undefined ? `${str(top.distance_km)} km` : '', top?.rating ? `★${str(top.rating)}` : '']),
    button: top ? { label: pick, send_text: poiPickText(c as never, str(top.name)) } : null,
  }
}

function placeList(c: AnyCard): DrivingSummary {
  const items: AnyCard[] = Array.isArray(c.items) ? c.items : []
  const top = items[0]
  return {
    icon: 'location',
    title: `周边 · ${str(c.category) || str(c.keyword) || '发现'}`,
    main: str(top?.name) || '附近没找到',
    sub: join([top?.distance_km !== undefined ? `${str(top.distance_km)} km` : '', top?.rating ? `★${str(top.rating)}` : '', top?.open_today ? `营业 ${str(top.open_today)}` : '']),
    button: top ? { label: '导航去第 1 个', send_text: navToText(str(top.name)) } : null,
  }
}

function placeDetail(c: AnyCard): DrivingSummary {
  return {
    icon: /餐|咖啡|食|饮|茶/.test(str(c.category)) ? 'dining' : 'location',
    title: str(c.name),
    main: c.open_today ? `营业 ${str(c.open_today)}` : str(c.address),
    sub: join([c.rating ? `★${str(c.rating)}` : '', c.cost ? `¥${str(c.cost)}/人` : '']),
    button: str(c.name) ? { label: '导航去这里', send_text: navToText(str(c.name)) } : null,
  }
}

function reminderList(c: AnyCard): DrivingSummary {
  const all: AnyCard[] = [...(Array.isArray(c.items) ? c.items : []), ...(Array.isArray(c.todos) ? c.todos : [])]
  const top = all[0]
  return {
    icon: 'clock',
    title: `提醒 · ${str(c.date_label) || (c.view === 'multi' ? '近期' : '今天')}`,
    main: str(top?.title) || '暂无提醒',
    sub: join([top?.time_display, all.length > 1 ? `另有 ${all.length - 1} 项` : '']),
    button: null,
  }
}

function reminderCard(c: AnyCard): DrivingSummary {
  const it = c.item || {}
  return {
    icon: 'clock',
    title: REMINDER_TITLE[c.context as keyof typeof REMINDER_TITLE] || '提醒',
    main: str(it.title),
    sub: join([it.time_display, it.recur_label]),
    button: firstButton(c.actions),
  }
}

function sceneCard(c: AnyCard): DrivingSummary {
  const steps: AnyCard[] = Array.isArray(c.actions_preview) ? c.actions_preview : []
  const state = SCENE_STATE[c.context as keyof typeof SCENE_STATE] || SCENE_STATE.created
  return {
    icon: 'layers',
    title: `场景 · ${str(c.name)}`,
    main: join([steps.length ? `${steps.length} 步` : '', state.label]),
    sub: join(steps.map((s) => s?.label)),
    button: null,
  }
}

function sceneList(c: AnyCard): DrivingSummary {
  const mine: AnyCard[] = Array.isArray(c.mine) ? c.mine : []
  const builtin: AnyCard[] = Array.isArray(c.builtin) ? c.builtin : []
  const top = mine[0] ?? builtin[0]
  return {
    icon: 'layers',
    title: `场景 · ${mine.length + builtin.length} 个`,
    main: str(top?.name) || '还没有场景',
    sub: join([mine.length ? `我建的 ${mine.length}` : '', builtin.length ? `内置 ${builtin.length}` : '']),
    button: top ? { label: `开启${str(top.name)}`, send_text: sceneOpenText(str(top.name)) } : null,
  }
}

function intentChoice(c: AnyCard): DrivingSummary {
  const n = Array.isArray(c.options) ? c.options.length : 0
  return {
    icon: 'info',
    title: '你是想…',
    main: str(c.question),
    sub: n >= 2 ? '说「第一个」或「第二个」' : '',
    button: null,
  }
}

function vision(c: AnyCard): DrivingSummary {
  const parts = str(c.answer).split(/[。！？，,；;\n]/).map((s) => s.trim()).filter(Boolean)
  return {
    icon: 'camera',
    title: '看一看',
    main: parts[0] || '没看清',
    // PoC 的画面来自手机摄像头，全量卡带「模拟画面」角标；行车摘要同样要说
    sub: join([parts[1], c.simulated ? '模拟画面' : parts[2]]),
    button: null,
  }
}

function manual(c: AnyCard): DrivingSummary {
  const chunk: AnyCard | undefined = Array.isArray(c.chunks) ? c.chunks[0] : undefined
  const path: string[] = Array.isArray(chunk?.section_path) ? chunk.section_path.map(str).filter(Boolean) : []
  const topic = path[path.length - 1] || str(c.document?.title) || '用户手册'
  return {
    icon: 'manual',
    title: `手册 · ${topic}`,
    main: firstSentence(chunk?.content) || '手册里没找到',
    sub: join([path.length > 1 ? path[0] : '', chunk?.page_start ? `第 ${str(chunk.page_start)} 页` : '']),
    button: null,
  }
}

function paymentQr(c: AnyCard, now: number): DrivingSummary {
  const at = Number(c.expires_at_ms || 0)
  const expired = at > 0 && now >= at
  const clock = at > 0 ? clockLabel(new Date(at).toISOString(), now) : ''
  return {
    icon: 'square',
    // 行车不出码（D11）：标题照全量卡的支付方式，主数值只给金额
    title: join([paymentPresentation(c).title, c.merchant || c.store_name || c.scene]),
    main: str(c.amount),
    sub: join([expired ? '已过期' : clock ? `${clock} 过期` : '', '停车后再付']),
    subWarn: expired,
    button: null,
  }
}

function paymentReceipt(c: AnyCard): DrivingSummary {
  return {
    icon: 'check-circle',
    title: '支付成功',
    main: str(c.amount) || '已收款',
    sub: join([c.scene, isPlaceholderOrderId(c.order_id) ? '' : c.order_id]),
    button: null,
  }
}

function parkingFee(c: AnyCard): DrivingSummary {
  return {
    icon: 'parking',
    title: '停车费 · 当前',
    main: str(c.amount),
    // 停车缴费能力声明的说法（agents/parking_payment/manifest.yaml examples，服务端追问也这么说）
    sub: join([c.plate, '说「交停车费」去付']),
    button: null,
  }
}

function merchantOrder(c: AnyCard): DrivingSummary {
  const o = normalizeMerchantOrder(c)
  const item = o.items[0]
  const status = orderStatusLabel(o.status)
  const isChoices = c.type === 'merchant_choices' || c.stage === 'choices'
  if (isChoices) {
    const options: AnyCard[] = Array.isArray(c.options) && c.options.length ? c.options : Array.isArray(c.items) ? c.items : []
    const top = options[0]
    // 主数值已经是第一个选项的名字，按钮再写一遍名字就重复了（P4d 真机 `ce70e36f`）：按钮叫「选这个」，句子不变
    const pick = firstButton(merchantActionButtons(c))
    return {
      icon: 'dining',
      title: `选择${o.brand}${c.choice_kind === 'store' ? '门店' : '商品'}`,
      main: str(top?.label) || str(top?.name) || '暂无选项',
      sub: join([top?.subtitle, options.length > 1 ? `共 ${options.length} 个` : '']),
      button: pick ? { label: '选这个', send_text: pick.send_text } : null,
    }
  }
  return {
    icon: 'dining',
    title: join([o.brand || '商户', status || (c.type === 'mcp_result' ? '商户服务' : '订单')]),
    main: o.amount ? `${c.type === 'merchant_checkout' ? '实付' : '应付'} ${o.amount}` : status || str(o.storeName) || '已查询',
    sub: join([item ? [item.name, item.specs].filter(Boolean).join(' ') : '', o.fulfillment || o.storeName]),
    button: firstButton(merchantActionButtons(c)),
  }
}

/** 兜底（未知卡型）：标题「其他结果」，主数值取第一个可显示字段，副行给第二个（中文字段名） */
function fallback(c: AnyCard): DrivingSummary {
  const [first, second] = cardPrimaryFields(c, 2)
  return {
    icon: 'info',
    title: '其他结果',
    main: first?.[1] ?? '已收到',
    sub: second ? `${fieldLabel(second[0])} · ${second[1]}` : '',
    button: cardPrimaryButton(c),
  }
}

const TEMPLATES: Readonly<Record<string, (c: AnyCard, now: number) => DrivingSummary>> = {
  weather,
  forecast,
  stock_quote: stock,
  news_list: (c) => newsLike('newspaper', `新闻 · ${str(c.topic)}`, Array.isArray(c.items) ? c.items : [], 'publish_time'),
  news_brief: (c) => newsLike('newspaper', `要闻 · ${str(c.topic)}`, Array.isArray(c.items) ? c.items : [], 'publish_time'),
  news_digest: (c) => newsLike('newspaper', `新闻摘要 · ${str(c.topic)}`, Array.isArray(c.headlines) ? c.headlines : [], 'publish_time'),
  search_answer: (c) => ({
    icon: 'search',
    title: `搜索 · ${str(c.query)}`,
    main: firstSentence(c.answer) || '没有找到结论',
    sub: Array.isArray(c.sources) && c.sources.length ? `${c.sources.length} 条来源` : '',
    button: null,
  }),
  search_result: searchResult,
  search_list: (c) => newsLike('search', `搜索结果 · ${str(c.query)}`, Array.isArray(c.items) ? c.items : [], ''),
  research_report: research,
  sports_scores: sportsScores,
  sports_scorers: sportsScorers,
  route_plan: routePlan,
  charging_route: chargingRoute,
  trip_itinerary: trip,
  poi_list: poiList,
  poi_detail: placeDetail,
  place_list: placeList,
  place_detail: placeDetail,
  reminder_list: reminderList,
  reminder_card: reminderCard,
  scene_card: sceneCard,
  scene_list: sceneList,
  intent_choice: intentChoice,
  vision_answer: vision,
  manual,
  payment_qr: paymentQr,
  payment_receipt: paymentReceipt,
  parking_fee: parkingFee,
  mcp_order: merchantOrder,
  mcp_result: merchantOrder,
  merchant_checkout: merchantOrder,
  merchant_choices: merchantOrder,
  merchant_order_preview: merchantOrder,
}

/** 有模板的卡型（守卫测试拿它与卡片注册表比：注册表里每个卡型都要有行车模板） */
export const DRIVING_TEMPLATE_TYPES: readonly string[] = [...Object.keys(TEMPLATES), 'card_group']

/** 任意卡 → 行车摘要。card_group 取主卡（display_priority 判据在 cardGroup.ts），副行补「另有 N 张卡」；
 *  未知卡型走兜底模板（绝不 null，同全量渲染的铁则）；只有空值才返回 null */
export function drivingSummary(card: unknown, now: number = Date.now()): DrivingSummary | null {
  if (!card || typeof card !== 'object') return null
  const c = card as AnyCard
  if (c.type === 'card_group') {
    const items: unknown[] = Array.isArray(c.items) ? c.items : []
    const { main } = splitCardGroup(items as never[])
    const inner = drivingSummary(main, now)
    if (!inner) return null
    const others = items.length - 1
    return others > 0 ? { ...inner, sub: [inner.sub, `另有 ${others} 张卡`].filter(Boolean).join(' · ') } : inner
  }
  const template = TEMPLATES[String(c.type || '')]
  return (template ?? fallback)(c, now)
}

