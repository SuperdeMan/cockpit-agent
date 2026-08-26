// 信息族卡片：weather / forecast / stock_quote / news×3 / search×3（M1-4 首批）
// + research_report / sports_scores / sports_scorers（M3-1）。
// 字段以 hmi/src/types.ts 为准；卡片给证据、气泡给结论（2026-06-22 信息卡重设计），不复读全文。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import type {
  ForecastCard,
  NewsBriefCard,
  NewsCard,
  NewsDigestCard,
  ResearchReportCard,
  SearchAnswerCard,
  SearchCard,
  SearchResultCard,
  SportsFixture,
  SportsScorersCard,
  SportsScoresCard,
  StockCard,
  WeatherCard,
} from '@shared/types.ts'

import type { Palette } from '../../ui/theme'
import { CardShell, Chip, FreshChip, KV, ProvBadge, relativeTime, type SendFn } from './parts'

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
      right={<FreshChip p={p} iso={card.freshness} />}
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
        <FreshChip p={p} iso={card.freshness} />
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

// ─────────────────────────── M3-1 增量 ───────────────────────────

/** 深度调研报告卡：分节可读报告——气泡给一段式简报，卡片给分节结论 + 引用 + 置信度 + gaps。
 *  手机上默认只展开第一节（车机是泊车看、手机是随手看，都不该一屏铺十屏）。 */
export function ResearchReport({ p, card }: { p: Palette; card: ResearchReportCard; onSend: SendFn }) {
  const [open, setOpen] = useState(0)
  const sections = card.sections || []
  return (
    <CardShell
      p={p}
      title={`调研 · ${card.question}`}
      right={card.overall_confidence ? <Chip p={p} text={`置信 ${card.overall_confidence}`} /> : undefined}
    >
      {card.summary ? (
        <Text style={{ color: p.fg2, fontSize: p.font(13), lineHeight: p.font(20) }}>{card.summary}</Text>
      ) : null}
      <View style={{ gap: 6 }}>
        {sections.map((s, i) => {
          const expanded = open === i
          return (
            <View key={i} style={{ borderTopWidth: i ? 1 : 0, borderColor: p.line, paddingTop: i ? 6 : 0 }}>
              <Pressable
                onPress={() => setOpen(expanded ? -1 : i)}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}
              >
                <Text style={{ color: p.fg3, fontSize: p.font(11), width: 14 }}>{expanded ? '▾' : '▸'}</Text>
                <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600', flex: 1 }} numberOfLines={2}>
                  {s.heading}
                </Text>
                {s.confidence ? <Chip p={p} text={s.confidence} /> : null}
              </Pressable>
              {expanded ? (
                <View style={{ paddingLeft: 20, gap: 4, marginTop: 4 }}>
                  <Text style={{ color: p.fg2, fontSize: p.font(12), lineHeight: p.font(19) }}>{s.body}</Text>
                  {s.citations?.length ? (
                    <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
                      引用 {s.citations.map((c) => `[${c}]`).join(' ')}
                    </Text>
                  ) : null}
                </View>
              ) : null}
            </View>
          )
        })}
      </View>
      {card.gaps?.length ? (
        <View style={{ gap: 2 }}>
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>未覆盖</Text>
          {card.gaps.slice(0, 3).map((g, i) => (
            <Text key={i} style={{ color: p.amber, fontSize: p.font(11) }} numberOfLines={2}>
              · {g}
            </Text>
          ))}
        </View>
      ) : null}
      {card.sources?.length ? (
        <View style={{ gap: 2 }}>
          {card.sources.slice(0, 6).map((s, i) => (
            <Text key={i} style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>
              [{s.idx ?? i + 1}] {s.title}
              {s.source ? ` · ${s.source}` : ''}
            </Text>
          ))}
        </View>
      ) : null}
      {relativeTime(card.freshness) ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{relativeTime(card.freshness)}</Text>
      ) : null}
    </CardShell>
  )
}

// 主客队配色与 HMI 同源（Cards.tsx:906-907）——两个客户端看同一场球不该是两种颜色
const HOME_C = '#5B8CFF'
const AWAY_C = '#9A6BFF'

/** 单场计分板：队名（国家队带旗）+ 大比分 + 状态；有进球明细时补 90 分钟时间轴 */
function FixtureBoard({ p, f }: { p: Palette; f: SportsFixture }) {
  const scored = (f.status === 'live' || f.status === 'finished') && (f.home_goals !== '' || f.away_goals !== '')
  const kickoff = f.kickoff && f.kickoff.includes('T') ? f.kickoff.slice(11, 16) : ''
  const goals = f.goals || []
  const team = (name: string, color: string, flag?: string) => (
    <View style={{ flex: 1, alignItems: 'center', gap: 6 }}>
      <View
        style={{
          width: 44,
          height: 44,
          borderRadius: 12,
          borderWidth: 2,
          borderColor: `${color}55`,
          backgroundColor: `${color}22`,
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Text style={{ fontSize: p.font(flag ? 24 : 14), fontWeight: '700', color }}>
          {flag || name.slice(0, 2)}
        </Text>
      </View>
      <Text style={{ color: p.fg2, fontSize: p.font(12), fontWeight: '600' }} numberOfLines={1}>
        {name}
      </Text>
    </View>
  )
  return (
    <View style={{ gap: 10, paddingVertical: 6 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        {team(f.home, HOME_C, f.home_flag)}
        <View style={{ alignItems: 'center', gap: 3, minWidth: 76 }}>
          {scored ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Text style={{ color: HOME_C, fontSize: p.font(28), fontWeight: '700' }}>{f.home_goals}</Text>
              <Text style={{ color: p.fg3, fontSize: p.font(16) }}>–</Text>
              <Text style={{ color: AWAY_C, fontSize: p.font(28), fontWeight: '700' }}>{f.away_goals}</Text>
            </View>
          ) : (
            <Text style={{ color: p.fg3, fontSize: p.font(14) }}>{kickoff || 'VS'}</Text>
          )}
          <Text
            style={{
              color: f.status === 'live' ? p.amber : p.fg3,
              fontSize: p.font(11),
              fontWeight: f.status === 'live' ? '700' : '400',
            }}
          >
            {f.status === 'live' && f.elapsed ? `${f.status_text} ${f.elapsed}分钟` : f.status_text}
          </Text>
        </View>
        {team(f.away, AWAY_C, f.away_flag)}
      </View>
      {goals.length ? (
        <View style={{ gap: 8, borderTopWidth: 1, borderColor: p.line, paddingTop: 10 }}>
          <Text style={{ color: p.fg3, fontSize: p.font(11), fontWeight: '600' }}>进球时间线</Text>
          <View style={{ height: 6, borderRadius: 3, backgroundColor: p.line, marginBottom: 6 }}>
            {goals.map((g, i) => {
              const m = Math.min(parseInt(g.minute, 10) || 0, 90)
              return (
                <View
                  key={i}
                  style={{
                    position: 'absolute',
                    left: `${(m / 90) * 100}%`,
                    top: -3,
                    marginLeft: -6,
                    width: 12,
                    height: 12,
                    borderRadius: 6,
                    backgroundColor: g.team === 'away' ? AWAY_C : HOME_C,
                  }}
                />
              )
            })}
          </View>
          {goals.map((g, i) => (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Text
                style={{
                  color: g.team === 'away' ? AWAY_C : HOME_C,
                  fontSize: p.font(11),
                  fontWeight: '700',
                  width: 34,
                  textAlign: 'right',
                }}
              >
                {g.minute}分
              </Text>
              <Text style={{ color: p.fg1, fontSize: p.font(12), flex: 1 }} numberOfLines={1}>
                ⚽ {g.player || '球员'}
              </Text>
              {g.detail && g.detail !== '进球' ? <Chip p={p} text={g.detail} /> : null}
            </View>
          ))}
        </View>
      ) : null}
    </View>
  )
}

export function SportsScores({ p, card }: { p: Palette; card: SportsScoresCard; onSend: SendFn }) {
  const fixtures = card.fixtures || []
  return (
    <CardShell p={p} title={card.title} right={<FreshChip p={p} iso={card.freshness} />}>
      {!fixtures.length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>暂无比赛安排</Text>
      ) : (
        fixtures.map((f, i) => (
          <View key={i} style={{ borderTopWidth: i ? 1 : 0, borderColor: p.line }}>
            <FixtureBoard p={p} f={f} />
          </View>
        ))
      )}
      {card.source ? <Text style={{ color: p.fg3, fontSize: p.font(10) }}>数据来源 {card.source}</Text> : null}
    </CardShell>
  )
}

export function SportsScorers({ p, card }: { p: Palette; card: SportsScorersCard; onSend: SendFn }) {
  const scorers = card.scorers || []
  return (
    <CardShell p={p} title={card.title} right={card.season ? <Chip p={p} text={card.season} /> : undefined}>
      {!scorers.length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>暂无射手榜数据</Text>
      ) : (
        scorers.map((s, i) => (
          <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 3 }}>
            <Text style={{ color: p.accent, fontSize: p.font(12), fontWeight: '700', width: 20 }}>{s.rank}</Text>
            <Text style={{ color: p.fg1, fontSize: p.font(13), flex: 1 }} numberOfLines={1}>
              {s.player}
            </Text>
            <Text style={{ color: p.fg3, fontSize: p.font(11), maxWidth: 96 }} numberOfLines={1}>
              {s.team}
            </Text>
            <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '700' }}>{s.goals}球</Text>
          </View>
        ))
      )}
      {card.source ? <Text style={{ color: p.fg3, fontSize: p.font(10) }}>数据来源 {card.source}</Text> : null}
    </CardShell>
  )
}
