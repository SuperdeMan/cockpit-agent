// 卡片渲染框架（实施计划 M1-4 ⛔）。
// 铁则（§2.6）：未知/未实现卡型渲染**兜底卡**（type 名 + 可识别主字段 + buttons + _prov），
// **绝不 null**——「桥从 M3 就在发、HMI 渲染 null 两个月」的欠账不许在 App 重演。
// 单卡异常不许抛崩整个列表（坑账 #6）：每张卡包 ErrorBoundary。
import { Component, type ReactNode } from 'react'
import { Text, View } from 'react-native'

import type { Palette } from '../../ui/theme'
import {
  Forecast,
  NewsBrief,
  NewsDigest,
  NewsList,
  SearchAnswer,
  SearchList,
  SearchResult,
  StockQuote,
  Weather,
} from './infoCards'
import { IntentChoice, ReminderList, ReminderSingle } from './miscCards'
import { PlaceDetail, PlaceList, PoiDetail, PoiList, RoutePlan } from './navCards'
import { CardButtons, CardShell, ProvBadge, type SendFn } from './parts'

/* eslint-disable @typescript-eslint/no-explicit-any */

interface CardProps {
  p: Palette
  card: any
  onSend: SendFn
}

// M1 首批 17 型 + search_list 顺手（§2.6 分批清单）。M3 补齐其余 12+。
const REGISTRY: Record<string, (props: CardProps) => ReactNode> = {
  card_group: ({ p, card, onSend }) => (
    <View style={{ gap: 8 }}>
      {(card.items || []).map((sub: any, i: number) => (
        <CardRenderer key={i} p={p} card={sub} onSend={onSend} />
      ))}
    </View>
  ),
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
}

/** 已实现卡型（守卫测试对照 §2.6 首批清单；M3 扩表时同步该测试） */
export const KNOWN_CARD_TYPES: string[] = Object.keys(REGISTRY)

// 兜底卡主字段的探取顺序：拿得出一个「人能认出这是什么」的值就够
const FALLBACK_PRIMARY_KEYS = [
  'title', 'name', 'question', 'query', 'topic', 'destination', 'answer', 'merchant',
  'brand', 'store_name', 'order_id', 'amount', 'status', 'city',
]

/** 兜底卡（铁则）：type 名 + 可识别主字段 + buttons + _prov，绝不 null */
export function FallbackCard({ p, card, onSend }: CardProps) {
  const fields = FALLBACK_PRIMARY_KEYS.map((k) => [k, card?.[k]] as const).filter(
    ([, v]) => typeof v === 'string' || typeof v === 'number',
  )
  return (
    <CardShell p={p} title={`卡片 · ${card?.type || '未知'}`} right={<ProvBadge p={p} prov={card?._prov} />}>
      {fields.slice(0, 4).map(([k, v]) => (
        <Text key={k} style={{ color: p.fg2, fontSize: p.font(12) }} numberOfLines={2}>
          {k}: {String(v)}
        </Text>
      ))}
      {!fields.length ? (
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
