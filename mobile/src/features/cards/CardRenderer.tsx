// 卡片渲染框架（实施计划 M1-4 ⛔）。
// 铁则（§2.6）：未知/未实现卡型渲染**兜底卡**（type 名 + 可识别主字段 + buttons + _prov），
// **绝不 null**——「桥从 M3 就在发、HMI 渲染 null 两个月」的欠账不许在 App 重演。
// 单卡异常不许抛崩整个列表（坑账 #6）：每张卡包 ErrorBoundary。
import { Component, type ReactNode } from 'react'
import { Text, View } from 'react-native'

import { cardListRows, cardPrimaryFields } from '../../core/cards/cardFields'
import type { Palette } from '../../ui/theme'
import {
  Forecast,
  NewsBrief,
  NewsDigest,
  NewsList,
  ResearchReport,
  SearchAnswer,
  SearchList,
  SearchResult,
  SportsScorers,
  SportsScores,
  StockQuote,
  Weather,
} from './infoCards'
import {
  McpOrder,
  McpResult,
  MerchantCheckout,
  ParkingFee,
  PaymentQr,
  PaymentReceipt,
} from './merchantCards'
import {
  IntentChoice,
  ManualEvidence,
  ReminderList,
  ReminderSingle,
  SceneList,
  SceneSingle,
  VisionAnswer,
} from './miscCards'
import {
  ChargingRoute,
  PlaceDetail,
  PlaceList,
  PoiDetail,
  PoiList,
  RoutePlan,
  TripItinerary,
} from './navCards'
import { CardGroup } from './CardGroup'
import { CardButtons, CardShell, ProvBadge, type SendFn } from './parts'

/* eslint-disable @typescript-eslint/no-explicit-any */

interface CardProps {
  p: Palette
  card: any
  onSend: SendFn
}

// 全量卡型（M1 首批 18 键 + M3-1 补齐 16 键；manual-rag v2 再补 manual 图文卡）。
// 守卫 test/cards.test.ts **从 types.ts 直接派生**清单逐字比对——不再手抄：
// 实施计划 §2.6 那句「29 型」就是手抄清单漂移的现成证据；当前数量继续由类型派生测试算。
const REGISTRY: Record<string, (props: CardProps) => ReactNode> = {
  card_group: ({ p, card, onSend }) => <CardGroup p={p} items={card.items || []} onSend={onSend} />,
  weather: ({ p, card, onSend }) => <Weather p={p} card={card} onSend={onSend} />,
  forecast: ({ p, card, onSend }) => <Forecast p={p} card={card} onSend={onSend} />,
  poi_list: ({ p, card, onSend }) => <PoiList p={p} card={card} onSend={onSend} />,
  poi_detail: ({ p, card, onSend }) => <PoiDetail p={p} card={card} onSend={onSend} />,
  place_list: ({ p, card, onSend }) => <PlaceList p={p} card={card} onSend={onSend} />,
  place_detail: ({ p, card, onSend }) => <PlaceDetail p={p} card={card} onSend={onSend} />,
  route_plan: ({ p, card, onSend }) => <RoutePlan p={p} card={card} onSend={onSend} />,
  intent_choice: ({ p, card, onSend }) => <IntentChoice p={p} card={card} onSend={onSend} />,
  reminder_list: ({ p, card, onSend }) => <ReminderList p={p} card={card} onSend={onSend} />,
  reminder_card: ({ p, card, onSend }) => <ReminderSingle p={p} card={card} onSend={onSend} />,
  stock_quote: ({ p, card, onSend }) => <StockQuote p={p} card={card} onSend={onSend} />,
  news_digest: ({ p, card, onSend }) => <NewsDigest p={p} card={card} onSend={onSend} />,
  news_brief: ({ p, card, onSend }) => <NewsBrief p={p} card={card} onSend={onSend} />,
  news_list: ({ p, card, onSend }) => <NewsList p={p} card={card} onSend={onSend} />,
  search_answer: ({ p, card, onSend }) => <SearchAnswer p={p} card={card} onSend={onSend} />,
  search_result: ({ p, card, onSend }) => <SearchResult p={p} card={card} onSend={onSend} />,
  search_list: ({ p, card, onSend }) => <SearchList p={p} card={card} onSend={onSend} />,

  // ── M3-1 增量 ──
  research_report: ({ p, card, onSend }) => <ResearchReport p={p} card={card} onSend={onSend} />,
  sports_scores: ({ p, card, onSend }) => <SportsScores p={p} card={card} onSend={onSend} />,
  sports_scorers: ({ p, card, onSend }) => <SportsScorers p={p} card={card} onSend={onSend} />,
  charging_route: ({ p, card, onSend }) => <ChargingRoute p={p} card={card} onSend={onSend} />,
  trip_itinerary: ({ p, card, onSend }) => <TripItinerary p={p} card={card} onSend={onSend} />,
  scene_card: ({ p, card, onSend }) => <SceneSingle p={p} card={card} onSend={onSend} />,
  scene_list: ({ p, card, onSend }) => <SceneList p={p} card={card} onSend={onSend} />,
  vision_answer: ({ p, card, onSend }) => <VisionAnswer p={p} card={card} onSend={onSend} />,
  manual: ({ p, card }) => <ManualEvidence p={p} card={card} />,
  payment_qr: ({ p, card, onSend }) => <PaymentQr p={p} card={card} onSend={onSend} />,
  payment_receipt: ({ p, card, onSend }) => <PaymentReceipt p={p} card={card} onSend={onSend} />,
  parking_fee: ({ p, card, onSend }) => <ParkingFee p={p} card={card} onSend={onSend} />,
  mcp_order: ({ p, card, onSend }) => <McpOrder p={p} card={card} onSend={onSend} />,
  mcp_result: ({ p, card, onSend }) => <McpResult p={p} card={card} onSend={onSend} />,
  // merchant 三个 type 字符串共用一个渲染器（types.ts:232 的联合类型，
  // 后两个是设计阶段卡名，桥侧仍在发——三个都注册，别让别名掉进兜底卡）
  merchant_checkout: ({ p, card, onSend }) => <MerchantCheckout p={p} card={card} onSend={onSend} />,
  merchant_choices: ({ p, card, onSend }) => <MerchantCheckout p={p} card={card} onSend={onSend} />,
  merchant_order_preview: ({ p, card, onSend }) => <MerchantCheckout p={p} card={card} onSend={onSend} />,
}

/** 已实现卡型。守卫测试拿它与 `hmi/src/types.ts::UiCard` 派生出的全量集合比对，
 *  两个方向都断言（缺=漏实现、多=types.ts 里没有这个型），所以**加卡型不需要改测试**。 */
export const KNOWN_CARD_TYPES: string[] = Object.keys(REGISTRY)

// 兜底卡主字段的探取顺序：拿得出一个「人能认出这是什么」的值就够
/** 兜底卡（铁则）：type 名 + 可识别主字段 + **通用列表行（items[]）** + buttons + _prov，绝不 null。
 *  B4-5：charging_list（types.ts 里没有、mobile 不能注册——cards.test 两向断言）落在这里时
 *  至少要能读出「哪几个站、多远、几个空闲」，而不是一句「暂未适配」。 */
export function FallbackCard({ p, card, onSend }: CardProps) {
  const fields = cardPrimaryFields(card)
  const rows = cardListRows(card)
  return (
    <CardShell p={p} title={`卡片 · ${card?.type || '未知'}`} right={<ProvBadge p={p} prov={card?._prov} />}>
      {fields.map(([k, v]) => (
        <Text key={k} style={{ color: p.fg2, fontSize: p.font(12) }} numberOfLines={2}>
          {k}: {v}
        </Text>
      ))}
      {rows.map((r, i) => (
        <View key={i} testID="fallback-row" style={{ flexDirection: 'row', gap: 8, alignItems: 'baseline' }}>
          <Text style={{ color: p.fg3, fontSize: p.font(12), width: 20 }}>{i + 1}</Text>
          <View style={{ flex: 1 }}>
            <Text style={{ color: p.fg1, fontSize: p.font(13) }} numberOfLines={1}>{r.title}</Text>
            {r.sub ? <Text style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>{r.sub}</Text> : null}
          </View>
        </View>
      ))}
      {!fields.length && !rows.length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>该卡型暂未适配，内容已收到</Text>
      ) : null}
      <CardButtons p={p} onSend={onSend} buttons={card?.buttons} />
    </CardShell>
  )
}

interface BoundaryProps extends CardProps {
  children: ReactNode
}

/** 渲染缺字段等单卡异常 → 换渲兜底卡，不抛崩消息列表 */
class CardBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch() {
    /* 单卡渲染异常已由兜底卡兜住，不上抛 */
  }
  render() {
    if (this.state.failed) {
      return <FallbackCard p={this.props.p} card={this.props.card} onSend={this.props.onSend} />
    }
    return this.props.children
  }
}

export function CardRenderer({ p, card, onSend }: CardProps) {
  if (!card || typeof card !== 'object') return null // 没有卡（≠未知卡型）才允许空
  const render = REGISTRY[card.type as string]
  return (
    <CardBoundary p={p} card={card} onSend={onSend}>
      {render ? render({ p, card, onSend }) : <FallbackCard p={p} card={card} onSend={onSend} />}
    </CardBoundary>
  )
}
