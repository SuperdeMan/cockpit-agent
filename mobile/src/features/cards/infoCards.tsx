// 信息族卡片（M1-4 首批）：weather / forecast / stock_quote / news×3 / search×3。
// 字段以 hmi/src/types.ts 为准；卡片给证据、气泡给结论（2026-06-22 信息卡重设计），不复读全文。
import { Text, View } from 'react-native'

import type {
  ForecastCard,
  NewsBriefCard,
  NewsCard,
  NewsDigestCard,
  SearchAnswerCard,
  SearchCard,
  SearchResultCard,
  StockCard,
  WeatherCard,
} from '@shared/types.ts'

import type { Palette } from '../../ui/theme'
import { CardShell, Chip, KV, ProvBadge, type SendFn } from './parts'

export function Weather({ p, card }: { p: Palette; card: WeatherCard; onSend: SendFn }) {
  const focus = card.focus
  return (
    <CardShell p={p} title={`天气 · ${card.city}`} right={<ProvBadge p={p} prov={card._prov} />}>
      {focus ? (
        <View style={{ gap: 4 }}>
          <Text style={{ color: p.fg1, fontSize: p.font(20), fontWeight: '700' }}>
            {focus.label} {focus.text_day} {focus.temp_low}~{focus.temp_high}°
          </Text>
          <Text style={{ color: p.fg3, fontSize: p.font(12) }}>
            今天实况 {card.temp}° {card.text}
          </Text>
        </View>
      ) : (
        <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 8 }}>
          <Text style={{ color: p.fg1, fontSize: p.font(30), fontWeight: '700' }}>{card.temp}°</Text>
          <Text style={{ color: p.fg2, fontSize: p.font(14), paddingBottom: 4 }}>{card.text}</Text>
        </View>
      )}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        <Chip p={p} text={`体感 ${card.feels_like}°`} />
        <Chip p={p} text={`湿度 ${card.humidity}%`} />
        <Chip p={p} text={`${card.wind_dir} ${card.wind_scale}级`} />
        {card.air_quality?.aqi ? (
          <Chip p={p} text={`AQI ${card.air_quality.aqi} ${card.air_quality.category || ''}`} />
        ) : null}
      </View>
      {(card.alerts || []).slice(0, 2).map((a, i) => (
        <Text key={i} style={{ color: p.amber, fontSize: p.font(12) }}>
          ⚠ {a.title}
        </Text>
      ))}
      {card.forecast?.length ? (
        <View style={{ flexDirection: 'row', gap: 10 }}>
          {card.forecast.slice(0, 3).map((d) => (
            <View key={d.date} style={{ flex: 1 }}>
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{d.date.slice(5)}</Text>
              <Text style={{ color: p.fg2, fontSize: p.font(12) }} numberOfLines={1}>
                {d.text_day}
              </Text>
              <Text style={{ color: p.fg2, fontSize: p.font(12) }}>
                {d.temp_low}~{d.temp_high}°
              </Text>
            </View>
          ))}
        </View>
      ) : null}
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>更新 {card.update_time}</Text>
    </CardShell>
  )
}

export function Forecast({ p, card }: { p: Palette; card: ForecastCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`未来预报 · ${card.city}`}>
      {(card.days || []).slice(0, 7).map((d) => (
        <View key={d.date} style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
          <Text style={{ color: p.fg3, fontSize: p.font(12), width: 52 }}>{d.date.slice(5)}</Text>
          <Text style={{ color: p.fg2, fontSize: p.font(13), flex: 1 }} numberOfLines={1}>
            {d.text_day} / {d.text_night}
          </Text>
          <Text style={{ color: p.fg1, fontSize: p.font(13) }}>
            {d.temp_low}~{d.temp_high}°
          </Text>
        </View>
      ))}
    </CardShell>
  )
}

export function StockQuote({ p, card }: { p: Palette; card: StockCard; onSend: SendFn }) {
  const up = !card.change.startsWith('-')
  const color = up ? p.red : p.green // A股惯例：红涨绿跌
  return (
    <CardShell p={p} title={`${card.name} · ${card.symbol}`} right={card.market ? <Chip p={p} text={card.market} /> : undefined}>
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 10 }}>
        <Text style={{ color, fontSize: p.font(26), fontWeight: '700' }}>{card.price}</Text>
        <Text style={{ color, fontSize: p.font(14), paddingBottom: 3 }}>
          {card.change} ({card.change_pct})
        </Text>
      </View>
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>行情时间 {card.market_time}</Text>
    </CardShell>
  )
}

function NewsRows({ p, items }: { p: Palette; items: Array<{ title: string; source: string; publish_time?: string; summary?: string }> }) {
  return (
    <View style={{ gap: 8 }}>
      {items.slice(0, 5).map((it, i) => (
        <View key={i} style={{ gap: 2 }}>
          <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }} numberOfLines={2}>
            {it.title}
          </Text>
          {it.summary ? (
            <Text style={{ color: p.fg2, fontSize: p.font(12) }} numberOfLines={2}>
              {it.summary}
            </Text>
          ) : null}
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            {it.source}
            {it.publish_time ? ` · ${it.publish_time}` : ''}
          </Text>
        </View>
      ))}
    </View>
  )
}

export function NewsList({ p, card }: { p: Palette; card: NewsCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`新闻 · ${card.topic}`}>
      {card.summary ? <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{card.summary}</Text> : null}
      <NewsRows p={p} items={card.items || []} />
    </CardShell>
  )
}

export function NewsDigest({ p, card }: { p: Palette; card: NewsDigestCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`新闻摘要 · ${card.topic}`}>
      <Text style={{ color: p.fg2, fontSize: p.font(13) }}>{card.summary}</Text>
      <View style={{ gap: 4 }}>
        {(card.headlines || []).slice(0, 6).map((h, i) => (
          <Text key={i} style={{ color: p.fg1, fontSize: p.font(12) }} numberOfLines={2}>
            · {h.title} <Text style={{ color: p.fg3 }}>—{h.source}</Text>
          </Text>
        ))}
      </View>
    </CardShell>
  )
}

export function NewsBrief({ p, card }: { p: Palette; card: NewsBriefCard; onSend: SendFn }) {
  return (
    <CardShell
      p={p}
      title={`要闻 · ${card.topic}`}
      right={card.freshness ? <Chip p={p} text={card.freshness} /> : undefined}
    >
      <NewsRows p={p} items={card.items || []} />
    </CardShell>
  )
}

export function SearchAnswer({ p, card }: { p: Palette; card: SearchAnswerCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`搜索 · ${card.query}`}>
      <Text style={{ color: p.fg1, fontSize: p.font(13), lineHeight: p.font(20) }}>{card.answer}</Text>
      <View style={{ gap: 2 }}>
        {(card.sources || []).slice(0, 4).map((s, i) => (
          <Text key={i} style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>
            [{i + 1}] {s.title} · {s.source}
          </Text>
        ))}
      </View>
    </CardShell>
  )
}

export function SearchResult({ p, card }: { p: Palette; card: SearchResultCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`检索证据 · ${card.query}`} right={<ProvBadge p={p} prov={card._prov} />}>
      <View style={{ gap: 6 }}>
        {(card.sources || []).slice(0, 5).map((s, i) => (
          <View key={i}>
            <Text style={{ color: p.fg1, fontSize: p.font(12) }} numberOfLines={2}>
              {s.title}
            </Text>
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
              {s.source}
              {s.published ? ` · ${s.published}` : ''}
            </Text>
          </View>
        ))}
      </View>
      <View style={{ flexDirection: 'row', gap: 6 }}>
        {card.freshness ? <Chip p={p} text={card.freshness} /> : null}
        {card.confidence ? <Chip p={p} text={`置信 ${card.confidence}`} /> : null}
      </View>
    </CardShell>
  )
}

export function SearchList({ p, card }: { p: Palette; card: SearchCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`搜索结果 · ${card.query}`}>
      {card.summary ? <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{card.summary}</Text> : null}
      <View style={{ gap: 6 }}>
        {(card.items || []).slice(0, 5).map((it, i) => (
          <View key={i}>
            <Text style={{ color: p.fg1, fontSize: p.font(12), fontWeight: '600' }} numberOfLines={2}>
              {it.title}
            </Text>
            <Text style={{ color: p.fg2, fontSize: p.font(11) }} numberOfLines={2}>
              {it.snippet}
            </Text>
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{it.source}</Text>
          </View>
        ))}
      </View>
    </CardShell>
  )
}
