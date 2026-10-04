// 信息族卡片：weather / forecast / stock_quote / news×3 / search×3（M1-4 首批）
// + research_report / sports_scores / sports_scorers（M3-1）。
// 字段以 hmi/src/types.ts 为准；卡片给证据、气泡给结论（2026-06-22 信息卡重设计），不复读全文。
import { Fragment, useState } from 'react'
import { Image, Pressable, Text, View } from 'react-native'

import type {
  ForecastCard,
  NewsBriefCard,
  NewsCard,
  NewsDigestCard,
  Provenance,
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

import { aqiLevel, clockLabel, dayLabel, kickoffLabel, skyIcon, teamAbbr, vendorName, weatherIcon } from '../../core/cards/cardMeta'
import type { IconName } from '../../ui/Icon'
import type { Palette } from '../../ui/theme'
import { RADIUS } from '../../ui/tokens'
import { Badge, CardIcon, CardShell, Chip, ConfBadge, FreshChip, ProvBadge, cardText, relativeTime, type SendFn } from './parts'

/** 天气卡（Figma weather / Card/MetricBlock）：Hero（numeric/xl 温度 + 天况 + 体感行）→ 指标块（湿度 / 风 / 空气质量，
 *  AQI 的图标与数值走 data/aqi 色阶）→ 预警行 → 三日预报（三列两端分布：今天 / 周X + 图标 + 区间）。
 *  问「明天天气」（focus）时指标块取焦点日的湿度与风，不放今天的实时 AQI——画板里焦点态那三块是从常态复制的。
 *  更新时刻在卡头的来源角标里；没有 `_prov` 时才在卡尾给「HH:mm 更新」 */
export function Weather({ p, card }: { p: Palette; card: WeatherCard; onSend: SendFn }) {
  const focus = card.focus
  const today = card.forecast?.[0]
  const aqi = focus ? '' : card.air_quality?.aqi
  const level = aqiLevel(aqi)
  const humidity = focus ? focus.humidity : card.humidity
  const windScale = focus ? focus.wind_scale : card.wind_scale
  const windDir = focus ? focus.wind_dir : card.wind_dir
  const tiles: { icon: IconName; value: string; label: string; color?: string }[] = []
  if (humidity) tiles.push({ icon: 'humidity', value: `${humidity}%`, label: '湿度' })
  if (windScale) tiles.push({ icon: 'wind', value: `${windScale} 级`, label: windDir || '风力' })
  if (aqi) {
    // 档位进标签行（v3 P7）：「27 优」放数值行时，窄舞台（约 80dp 一格）截成「27 …」，「156 中度污染」在对话列里也放不下；
    // 数值行只放数字，标签行「空气优」/「中度污染」（四字档位不再加前缀），颜色仍随档位
    const category = (card.air_quality?.category || '').trim()
    const label = !category ? '空气质量' : category.length <= 2 ? `空气${category}` : category
    tiles.push({ icon: 'air-quality', value: String(aqi), label, color: level === null ? undefined : p.aqi[level] })
  }
  return (
    <CardShell p={p} icon={weatherIcon(card)} title={`天气 · ${card.city}`} right={<ProvBadge p={p} prov={card._prov} />}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        <Text style={[cardText(p, 'numericXl'), { color: p.fg1 }]}>
          {focus ? `${focus.temp_low}–${focus.temp_high}°` : `${card.temp}°`}
        </Text>
        <View style={{ flex: 1 }}>
          <Text style={[cardText(p, 'bodyM'), { color: p.fg1 }]} numberOfLines={1}>
            {focus ? `${focus.label} ${focus.text_day}` : card.text}
          </Text>
          <Text style={[cardText(p, 'caption'), { color: p.fg3 }]} numberOfLines={1}>
            {focus
              ? `今天实况 ${card.temp}° ${card.text}`
              : `体感 ${card.feels_like}°${today ? ` · ${today.temp_low}–${today.temp_high}°` : ''}`}
          </Text>
        </View>
      </View>
      {tiles.length ? (
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {tiles.map((t) => (
            <View
              key={t.icon}
              style={{ flex: 1, backgroundColor: p.surfaceHigh, borderRadius: RADIUS.md, paddingVertical: 10, paddingHorizontal: 12, gap: 2 }}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                <CardIcon p={p} name={t.icon} size={16} color={t.color ?? p.fg2} />
                <Text style={[cardText(p, 'numericM'), { color: t.color ?? p.fg1, flexShrink: 1 }]} numberOfLines={1}>
                  {t.value}
                </Text>
              </View>
              <Text style={[cardText(p, 'caption'), { color: p.fg3 }]} numberOfLines={1}>
                {t.label}
              </Text>
            </View>
          ))}
        </View>
      ) : null}
      {(card.alerts || []).slice(0, 2).map((a, i) => (
        <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <CardIcon p={p} name="warning" size={16} color={p.amber} />
          <Text style={[cardText(p, 'bodyM'), { color: p.amber, flexShrink: 1 }]}>{a.title}</Text>
        </View>
      ))}
      {card.forecast?.length ? (
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          {card.forecast.slice(0, 3).map((d) => (
            <View key={d.date} style={{ alignItems: 'center', gap: 4, minWidth: 50 }}>
              <Text style={[cardText(p, 'caption'), { color: p.fg3 }]}>{dayLabel(d.date)}</Text>
              <CardIcon p={p} name={skyIcon(d.text_day)} size={18} color={p.fg2} />
              <Text style={[cardText(p, 'numericM'), { color: p.fg1 }]}>
                {d.temp_low}–{d.temp_high}°
              </Text>
            </View>
          ))}
        </View>
      ) : null}
      {card._prov ? null : <FreshChip p={p} iso={card.update_time} />}
    </CardShell>
  )
}

/** 未来预报（Figma forecast）：每行 日 + 图标 + 天况 + 低温 + 区间条 + 高温。区间条在这几天的最低 / 最高温之间定位
 *  （视觉，数据已有）；温度解析不了的行不画条，只留轨道 */
export function Forecast({ p, card }: { p: Palette; card: ForecastCard; onSend: SendFn }) {
  const days = (card.days || []).slice(0, 7)
  const lows = days.map((d) => Number.parseFloat(d.temp_low))
  const highs = days.map((d) => Number.parseFloat(d.temp_high))
  const known = [...lows, ...highs].filter(Number.isFinite)
  const min = Math.min(...known)
  const span = Math.max(...known) - min
  return (
    // D19：预报卡补数据源角标（类型上没声明 _prov，后端实际下发就显示）
    <CardShell p={p} icon="weather-cloudy" title={`未来预报 · ${card.city}`} right={<ProvBadge p={p} prov={(card as { _prov?: Provenance })._prov} />}>
      {days.map((d, i) => {
        const lo = lows[i]
        const hi = highs[i]
        const bar = Number.isFinite(lo) && Number.isFinite(hi) && hi >= lo
        const left = bar && span > 0 ? ((lo - min) / span) * 100 : 0
        const width = bar && span > 0 ? Math.max(((hi - lo) / span) * 100, 6) : 100
        return (
          <View key={d.date} style={{ flexDirection: 'row', gap: 10, alignItems: 'center', minHeight: 24 }}>
            <Text style={[cardText(p, 'bodyM'), { color: p.fg1, minWidth: 40 }]}>{dayLabel(d.date)}</Text>
            <CardIcon p={p} name={skyIcon(d.text_day)} size={18} color={p.fg2} />
            <Text style={[cardText(p, 'caption'), { color: p.fg2, width: 64 }]} numberOfLines={1}>
              {!d.text_night || d.text_day === d.text_night ? d.text_day : `${d.text_day} / ${d.text_night}`}
            </Text>
            <Text style={[cardText(p, 'numericM'), { color: p.fg3, minWidth: 28, textAlign: 'right' }]}>{d.temp_low}°</Text>
            <View style={{ flex: 1, height: 6, borderRadius: 3, backgroundColor: p.surfaceHighest }}>
              {bar ? (
                <View
                  style={{ position: 'absolute', top: 0, bottom: 0, left: `${Math.min(left, 100 - width)}%`, width: `${width}%`, borderRadius: 3, backgroundColor: p.accent }}
                />
              ) : null}
            </View>
            <Text style={[cardText(p, 'numericM'), { color: p.fg1, minWidth: 28, textAlign: 'right' }]}>{d.temp_high}°</Text>
          </View>
        )
      })}
    </CardShell>
  )
}

export function StockQuote({ p, card }: { p: Palette; card: StockCard; onSend: SendFn }) {
  // 涨跌色走数据色（D10「涨跌色变量化」：dataUp / dataDown 按主题过对比度；A股惯例红涨绿跌）；平盘不着色
  const flat = /^[+-]?0+(\.0+)?$/.test(card.change.trim())
  const color = flat ? p.fg2 : card.change.startsWith('-') ? p.dataDown : p.dataUp
  return (
    <CardShell p={p} icon="building" title={`${card.name} · ${card.symbol}`} right={card.market ? <Chip p={p} text={card.market} /> : undefined}>
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 10 }}>
        <Text style={{ color, fontSize: p.font(26), fontWeight: '700' }}>{card.price}</Text>
        <Text style={{ color, fontSize: p.font(14), paddingBottom: 3 }}>
          {card.change} ({card.change_pct})
        </Text>
      </View>
      <Text style={{ color: p.fg3, fontSize: p.font(12) }}>行情 {clockLabel(card.market_time) || card.market_time}</Text>
    </CardShell>
  )
}

function NewsRows({ p, items }: { p: Palette; items: { title: string; source: string; publish_time?: string; summary?: string }[] }) {
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
            {it.publish_time ? ` · ${clockLabel(it.publish_time) || it.publish_time}` : ''}
          </Text>
        </View>
      ))}
    </View>
  )
}

export function NewsList({ p, card }: { p: Palette; card: NewsCard; onSend: SendFn }) {
  return (
    <CardShell p={p} icon="newspaper" title={`新闻 · ${card.topic}`}>
      {card.summary ? <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{card.summary}</Text> : null}
      <NewsRows p={p} items={card.items || []} />
    </CardShell>
  )
}

export function NewsDigest({ p, card }: { p: Palette; card: NewsDigestCard; onSend: SendFn }) {
  return (
    <CardShell p={p} icon="newspaper" title={`新闻摘要 · ${card.topic}`}>
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
      icon="newspaper"
      title={`要闻 · ${card.topic}`}
      right={<FreshChip p={p} iso={card.freshness} />}
    >
      <NewsRows p={p} items={card.items || []} />
    </CardShell>
  )
}

export function SearchAnswer({ p, card }: { p: Palette; card: SearchAnswerCard; onSend: SendFn }) {
  return (
    <CardShell p={p} icon="search" title={`搜索 · ${card.query}`}>
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
    <CardShell p={p} icon="search" title={`检索证据 · ${card.query}`} right={<ProvBadge p={p} prov={card._prov} />}>
      <View style={{ gap: 6 }}>
        {(card.sources || []).slice(0, 5).map((s, i) => (
          <View key={i}>
            <Text style={{ color: p.fg1, fontSize: p.font(12) }} numberOfLines={2}>
              {s.title}
            </Text>
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
              {s.source}
              {s.published ? ` · ${clockLabel(s.published) || s.published}` : ''}
            </Text>
          </View>
        ))}
      </View>
      <View style={{ flexDirection: 'row', gap: 6 }}>
        <FreshChip p={p} iso={card.freshness} />
        <ConfBadge p={p} level={card.confidence} />
      </View>
    </CardShell>
  )
}

export function SearchList({ p, card }: { p: Palette; card: SearchCard; onSend: SendFn }) {
  return (
    <CardShell p={p} icon="search" title={`搜索结果 · ${card.query}`}>
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
      icon="research"
      title={`调研 · ${card.question}`}
      right={<ConfBadge p={p} level={card.overall_confidence} />}
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
                <ConfBadge p={p} level={s.confidence} />
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

/** 队伍标识（D18，Figma team/*）：36 圆（surface/highest）+ 2px 主客色环里放缩写——原来取名字前两个字，英文队名会变成「Ma」；
 *  有 https 队徽时画 32px 队徽（加载失败回落缩写圆）；国家队有国旗 emoji 时放国旗。主客色取 Palette.series[1] / [2]
 *  （Figma data/series/2 / 3；深色与 HMI Cards.tsx:906-907 同值，浅色换成过对比度的深一档）。
 *  队名在下方：分出胜负的已结束比赛里胜方用 label/m 主色，其余 caption 次级色。
 *  圆与队徽随字号档放大（p.font）：缩写走 label/m 字阶，大字档两个汉字约 32dp，固定 36 圆（2px 环内径 32）会压到环上 */
function TeamBadge({
  p,
  name,
  color,
  flag,
  logo,
  winner,
}: {
  p: Palette
  name: string
  color: string
  flag?: string
  logo?: string
  winner: boolean
}) {
  const [logoFailed, setLogoFailed] = useState(false)
  const showLogo = !!logo && /^https:\/\//.test(logo) && !logoFailed
  const d = p.font(36)
  const logoSize = p.font(32)
  return (
    <View style={{ flex: 1, alignItems: 'center', gap: 6 }}>
      <View
        style={[
          { width: d, height: d, borderRadius: d / 2, alignItems: 'center', justifyContent: 'center' },
          showLogo ? null : { borderWidth: 2, borderColor: color, backgroundColor: p.surfaceHighest },
        ]}
      >
        {showLogo ? (
          <Image source={{ uri: logo }} style={{ width: logoSize, height: logoSize }} resizeMode="contain" onError={() => setLogoFailed(true)} />
        ) : (
          <Text style={[cardText(p, flag ? 'titleM' : 'labelM'), { color: p.fg1 }]}>{flag || teamAbbr(name)}</Text>
        )}
      </View>
      <Text style={[cardText(p, winner ? 'labelM' : 'caption'), { color: winner ? p.fg1 : p.fg2, textAlign: 'center' }]} numberOfLines={1}>
        {name}
      </Text>
    </View>
  )
}

/** 单场计分板（Figma sports_scores/*）：主队 · 比分（numeric/l 主色）+ 状态 · 客队；进行中状态前加琥珀圆点。
 *  有进球明细时补时间线：0–90' 轨道上按主客色落点 + 逐球一行（分钟 · 主客色足球图标 · 球员 · 点球 / 乌龙标签） */
function FixtureBoard({ p, f }: { p: Palette; f: SportsFixture }) {
  const home = p.series[1]
  const away = p.series[2]
  const live = f.status === 'live'
  const scored = (live || f.status === 'finished') && (f.home_goals !== '' || f.away_goals !== '')
  const hg = Number.parseInt(f.home_goals, 10)
  const ag = Number.parseInt(f.away_goals, 10)
  const decided = f.status === 'finished' && Number.isFinite(hg) && Number.isFinite(ag) && hg !== ag
  const goals = f.goals || []
  const teamColor = (side: string) => (side === 'away' ? away : side === 'home' ? home : p.fg2)
  return (
    <View style={{ gap: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
        <TeamBadge p={p} name={f.home} color={home} flag={f.home_flag} logo={f.home_logo} winner={decided && hg > ag} />
        <View style={{ alignItems: 'center', gap: 2, minWidth: 66 }}>
          {scored ? (
            <Text style={[cardText(p, 'numericL'), { color: p.fg1 }]}>
              {f.home_goals || '0'} : {f.away_goals || '0'}
            </Text>
          ) : (
            <Text style={[cardText(p, 'numericM'), { color: p.fg1, lineHeight: cardText(p, 'numericL').lineHeight }]}>
              {kickoffLabel(f.kickoff) || 'VS'}
            </Text>
          )}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
            {live ? <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: p.amber }} /> : null}
            <Text style={[cardText(p, 'caption'), { color: live ? p.amber : p.fg3 }]}>
              {live && f.elapsed ? `${f.status_text} ${f.elapsed}'` : f.status_text}
            </Text>
          </View>
        </View>
        <TeamBadge p={p} name={f.away} color={away} flag={f.away_flag} logo={f.away_logo} winner={decided && ag > hg} />
      </View>
      {goals.length ? (
        <View style={{ gap: 8 }}>
          <Text style={[cardText(p, 'labelM'), { color: p.fg3 }]}>进球时间线</Text>
          <View style={{ height: 14, justifyContent: 'center' }}>
            <View style={{ height: 4, borderRadius: 2, backgroundColor: p.surfaceHighest }} />
            {goals.map((g, i) => {
              const m = Math.min(Number.parseInt(g.minute, 10) || 0, 90)
              return (
                <View
                  key={i}
                  style={{
                    position: 'absolute',
                    top: 1,
                    left: `${(m / 90) * 100}%`,
                    marginLeft: -6,
                    width: 12,
                    height: 12,
                    borderRadius: 6,
                    backgroundColor: teamColor(g.team),
                  }}
                />
              )
            })}
          </View>
          {goals.map((g, i) => (
            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, minHeight: 24 }}>
              <Text style={[cardText(p, 'numericM'), { color: p.fg2, minWidth: 36 }]}>{`${g.minute}'`}</Text>
              <CardIcon p={p} name="football" size={16} color={teamColor(g.team)} />
              <Text style={[cardText(p, 'bodyM'), { color: p.fg1, flex: 1 }]} numberOfLines={1}>
                {g.player || '球员'}
              </Text>
              {g.detail && g.detail !== '进球' ? <Badge p={p} tone="neutral" text={g.detail} /> : null}
            </View>
          ))}
        </View>
      ) : null}
    </View>
  )
}

function SourceLine({ p, source }: { p: Palette; source?: string }) {
  if (!source) return null
  return <Text style={[cardText(p, 'caption'), { color: p.fg3 }]}>数据来源 · {vendorName(source)}</Text>
}

export function SportsScores({ p, card }: { p: Palette; card: SportsScoresCard; onSend: SendFn }) {
  const fixtures = card.fixtures || []
  return (
    <CardShell p={p} icon="football" title={card.title} right={<FreshChip p={p} iso={card.freshness} />}>
      {!fixtures.length ? (
        <Text style={[cardText(p, 'bodyM'), { color: p.fg2 }]}>暂无比赛安排</Text>
      ) : (
        fixtures.map((f, i) => (
          <Fragment key={i}>
            {i ? <View style={{ height: 1, backgroundColor: p.line }} /> : null}
            <FixtureBoard p={p} f={f} />
          </Fragment>
        ))
      )}
      <SourceLine p={p} source={card.source} />
    </CardShell>
  )
}

/** 射手榜（Figma sports_scorers）：排名圆（第一名 accent/soft 底）+ 球员 label/l / 球队 caption + 进球数；卡头右槽赛季标签 */
export function SportsScorers({ p, card }: { p: Palette; card: SportsScorersCard; onSend: SendFn }) {
  const scorers = card.scorers || []
  const season = card.season ? (card.season.includes('赛季') ? card.season : `${card.season} 赛季`) : ''
  return (
    <CardShell p={p} icon="trophy" title={card.title} right={season ? <Badge p={p} tone="neutral" text={season} /> : undefined}>
      {!scorers.length ? (
        <Text style={[cardText(p, 'bodyM'), { color: p.fg2 }]}>暂无射手榜数据</Text>
      ) : (
        scorers.map((s, i) => (
          <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 36 }}>
            <View
              style={{
                width: 24,
                height: 24,
                borderRadius: 12,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: s.rank === 1 ? p.accentSoft : p.surfaceHighest,
              }}
            >
              <Text style={[cardText(p, 'labelM'), { color: s.rank === 1 ? p.accent : p.fg2 }]}>{s.rank}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[cardText(p, 'labelL'), { color: p.fg1 }]} numberOfLines={1}>
                {s.player}
              </Text>
              {s.team ? (
                <Text style={[cardText(p, 'caption'), { color: p.fg3 }]} numberOfLines={1}>
                  {s.team}
                </Text>
              ) : null}
            </View>
            <Text style={[cardText(p, 'numericM'), { color: p.fg1 }]}>{s.goals} 球</Text>
          </View>
        ))
      )}
      <SourceLine p={p} source={card.source} />
    </CardShell>
  )
}
