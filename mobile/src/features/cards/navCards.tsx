// 出行族卡片：poi_list（dest/waypoint 变体）/ poi_detail / place_list / place_detail /
// route_plan（estimate/cancelled 变体）（M1-4 首批）+ charging_route / trip_itinerary（M3-1）。
// 行点按=合成与语音「第N个」同语义的一句话经普通 send（types.ts:52-58）；
// place 详情透传高德 POI id（不按名重搜取到别的分店——nearby 重构的老账）。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import { Link } from 'expo-router'

import { placeMenuAction } from '@shared/merchantUi.mjs'
import type {
  ChargingRouteCard,
  PlaceDetailCard,
  PlaceListCard,
  PoiDetailCard,
  PoiListCard,
  RoutePlanCard,
  TripItineraryCard,
} from '@shared/types.ts'

import { MAP_AVAILABLE, mapPointsOf, toMapPoint, type MapPoint } from '../../core/map/available'
import type { Palette } from '../../ui/theme'
import { CardButtons, CardShell, Chip, KV, ProvBadge, type SendFn } from './parts'

function ItemRow({
  p,
  idx,
  title,
  sub,
  chips,
  action,
  onPress,
}: {
  p: Palette
  idx: number
  title: string
  sub?: string
  chips?: string[]
  /** 行内直达按钮（如品牌门店的「看菜单」），点它不触发整行的 onPress */
  action?: { label: string; onPress: () => void }
  onPress?: () => void
}) {
  return (
    <Pressable
      onPress={onPress}
      style={{ flexDirection: 'row', gap: 8, paddingVertical: 4, alignItems: 'flex-start' }}
    >
      <Text style={{ color: p.accent, fontSize: p.font(13), width: 20, fontWeight: '700' }}>
        {idx + 1}
      </Text>
      <View style={{ flex: 1, gap: 2 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: p.fg1, fontSize: p.font(14), fontWeight: '600', flex: 1 }} numberOfLines={1}>
            {title}
          </Text>
          {action ? (
            <Pressable
              onPress={action.onPress}
              hitSlop={6}
              style={{
                paddingHorizontal: 9,
                paddingVertical: 3,
                borderRadius: 999,
                borderWidth: 1,
                borderColor: p.accent,
              }}
            >
              <Text style={{ color: p.accent, fontSize: p.font(11), fontWeight: '600' }}>{action.label}</Text>
            </Pressable>
          ) : null}
        </View>
        {sub ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>
            {sub}
          </Text>
        ) : null}
        {chips?.length ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
            {chips.map((c, i) => (
              <Chip key={i} p={p} text={c} />
            ))}
          </View>
        ) : null}
      </View>
    </Pressable>
  )
}

/** 「地图」入口（M3-3）。三个条件缺一不出现：地图能力可用 / 这张卡真的带坐标 /
 *  至少有一个点。**不可用时入口根本不渲染**——卡片信息面零损失，
 *  用户看不到一个按了会失望的按钮，这就是计划说的「可降级」。 */
function MapEntry({ p, points, title }: { p: Palette; points: MapPoint[]; title: string }) {
  if (!MAP_AVAILABLE || !points.length) return null
  return (
    <Link
      href={{ pathname: '/map', params: { points: JSON.stringify(points), title } }}
      style={{
        alignSelf: 'flex-start',
        color: p.accent,
        fontSize: p.font(12),
        paddingHorizontal: 10,
        paddingVertical: 5,
      }}
    >
      地图
    </Link>
  )
}

export function PoiList({ p, card, onSend }: { p: Palette; card: PoiListCard; onSend: SendFn }) {
  const purpose = card.purpose
  const title =
    card.title ||
    (purpose === 'dest_choice'
      ? '选择充电目的地'
      : purpose === 'waypoint_choice'
        ? `顺路停靠 · 去${card.destination || ''}`
        : `候选 · ${card.keyword || 'POI'}`)
  const hint =
    purpose === 'dest_choice'
      ? '点选或说「第N个」回填目的地'
      : purpose === 'waypoint_choice'
        ? '点选或说「第N个」加为途经点'
        : '点选或说「第N个」开始导航'
  const sendFor = (name: string) =>
    purpose === 'dest_choice'
      ? name
      : purpose === 'waypoint_choice'
        ? `导航去${card.destination || ''}途经${name}`
        : `导航去${name}`
  return (
    <CardShell p={p} title={title}>
      {(card.items || []).map((it, i) => (
        <ItemRow
          key={it.id || i}
          p={p}
          idx={i}
          title={it.name}
          sub={it.address}
          chips={[
            ...(it.rating ? [`★${it.rating}`] : []),
            ...(it.distance_km !== undefined ? [`${it.distance_km}km`] : []),
          ]}
          onPress={() => onSend(sendFor(it.name))}
        />
      ))}
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{hint}</Text>
    </CardShell>
  )
}

export function PoiDetail({ p, card, onSend }: { p: Palette; card: PoiDetailCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title="地点详情">
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '700' }}>{card.name}</Text>
      <KV p={p} k="地址" v={card.address} />
      <KV p={p} k="类型" v={card.category} />
      <KV p={p} k="评分" v={card.rating ? `★${card.rating}` : ''} />
      <MapEntry p={p} points={[toMapPoint(card)].filter((x) => x !== null)} title={card.name} />
      <CardButtons p={p} onSend={onSend} buttons={[{ label: '导航去这里', send_text: `导航去${card.name}` }]} />
    </CardShell>
  )
}

export function PlaceList({ p, card, onSend }: { p: Palette; card: PlaceListCard; onSend: SendFn }) {
  return (
    <CardShell
      p={p}
      title={`周边 · ${card.category || card.keyword || '发现'}`}
      right={<ProvBadge p={p} prov={card._prov} />}
    >
      {(card.items || []).map((it, i) => (
        <ItemRow
          key={it.id || i}
          p={p}
          idx={i}
          title={it.name}
          sub={it.address}
          chips={[
            ...(it.rating ? [`★${it.rating}`] : []),
            ...(it.cost ? [`¥${it.cost}/人`] : []),
            ...(it.distance_km !== undefined ? [`${it.distance_km}km`] : []),
            ...(it.open_today ? [it.open_today] : []),
          ]}
          // 品牌门店（瑞幸/麦当劳）补「看菜单」直达：发现→看单→点单三步全可点按。
          // 句式由共享的 placeMenuAction 决定（与范例库闭环），App 不自己拼——
          // M3-1 真机首轮发现 App 缺这个按钮而 HMI 有，那条链在手机上是断的。
          action={(() => {
            const menu = placeMenuAction(it.name)
            return menu ? { label: menu.label, onPress: () => onSend(menu.send_text) } : undefined
          })()}
          onPress={() => onSend(`看${it.name}的详情`, it.id ? { nearby_poi_id: it.id } : undefined)}
        />
      ))}
      <MapEntry
        p={p}
        points={mapPointsOf(card.items)}
        title={`周边 · ${card.category || card.keyword || '发现'}`}
      />
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
        点选看详情；说「导航去第N个」直接导航；「换一批」看更多
      </Text>
    </CardShell>
  )
}

export function PlaceDetail({ p, card, onSend }: { p: Palette; card: PlaceDetailCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title="周边详情" right={<ProvBadge p={p} prov={card._prov} />}>
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '700' }}>{card.name}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
        {card.rating ? <Chip p={p} text={`★${card.rating}`} /> : null}
        {card.cost ? <Chip p={p} text={`¥${card.cost}/人`} /> : null}
        {(card.tags || '')
          .split(',')
          .filter(Boolean)
          .slice(0, 4)
          .map((t, i) => (
            <Chip key={i} p={p} text={t.trim()} />
          ))}
      </View>
      <KV p={p} k="地址" v={card.address} />
      <KV p={p} k="电话" v={card.tel} />
      <KV p={p} k="今日营业" v={card.open_today} />
      <KV p={p} k="每周" v={card.open_week} />
      <MapEntry p={p} points={[toMapPoint(card)].filter((x) => x !== null)} title={card.name} />
      <CardButtons p={p} onSend={onSend} buttons={[{ label: '导航去这里', send_text: `导航去${card.name}` }]} />
    </CardShell>
  )
}

export function RoutePlan({ p, card, onSend }: { p: Palette; card: RoutePlanCard; onSend: SendFn }) {
  // 卡片标题必须与本轮真实动作一致（I-016/I-022）：只算不导≠已在导航；已取消要说清作废
  const title = card.cancelled ? '导航已结束' : card.estimate ? '路线测算（未开始导航）' : '路线已规划'
  return (
    <CardShell p={p} title={title}>
      <Text style={{ color: card.cancelled ? p.fg3 : p.fg1, fontSize: p.font(15), fontWeight: '600' }}>
        {card.origin ? `${card.origin} → ` : ''}
        {card.destination}
      </Text>
      {card.waypoints?.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 4 }}>
          {card.waypoints.map((w, i) => (
            <Chip key={i} p={p} text={`途经 ${w.name}`} tone="accent" />
          ))}
        </View>
      ) : null}
      <View style={{ flexDirection: 'row', gap: 6 }}>
        {card.distance_km !== undefined ? <Chip p={p} text={`${card.distance_km}km`} /> : null}
        {card.duration_min !== undefined ? <Chip p={p} text={`约${card.duration_min}分钟`} /> : null}
        {card.eta_ts ? (
          <Chip p={p} text={`预计 ${new Date(card.eta_ts * (card.eta_ts > 1e11 ? 1 : 1000)).toTimeString().slice(0, 5)} 到`} />
        ) : null}
      </View>
      {card.estimate && !card.cancelled ? (
        <CardButtons p={p} onSend={onSend} buttons={[{ label: '开始导航', send_text: `导航去${card.destination}` }]} />
      ) : null}
    </CardShell>
  )
}

// ─────────────────────────── M3-1 增量 ───────────────────────────

/** 充电路线卡：出发地 → 沿途补电点（带 at_km）→ 目的地。
 *  stops 为空**不是空状态**而是一条结论——「全程无需补电」，照 HMI 同判据渲染；
 *  渲染成「暂无数据」会把一个肯定回答说成查询失败。 */
export function ChargingRoute({ p, card }: { p: Palette; card: ChargingRouteCard; onSend: SendFn }) {
  const min = card.duration_min || 0
  const dur = min
    ? `${Math.floor(min / 60) ? `${Math.floor(min / 60)}小时` : ''}${min % 60 ? `${min % 60}分钟` : ''}`
    : ''
  const stops = card.stops || []
  return (
    <CardShell
      p={p}
      title="充电路线规划"
      right={
        card.distance_km ? <Chip p={p} text={`${card.distance_km}km${dur ? ` · ${dur}` : ''}`} /> : undefined
      }
    >
      {stops.length ? (
        <View style={{ gap: 6 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: p.accent }} />
            <View>
              <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }}>出发地</Text>
              {card.soc ? (
                <Text style={{ color: p.fg3, fontSize: p.font(11) }}>当前电量 {card.soc}</Text>
              ) : null}
            </View>
          </View>
          {stops.map((s, i) => (
            <View key={i} style={{ gap: 4 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingLeft: 4 }}>
                <View style={{ width: 1, height: 18, backgroundColor: p.line }} />
                {s.at_km != null ? (
                  <Text style={{ color: p.fg3, fontSize: p.font(11) }}>约 {s.at_km}km 处</Text>
                ) : null}
              </View>
              <View
                style={{
                  flexDirection: 'row',
                  gap: 10,
                  alignItems: 'flex-start',
                  padding: 12,
                  borderRadius: 14,
                  backgroundColor: p.amberSoft,
                  borderWidth: 1,
                  borderColor: p.amberSoft,
                }}
              >
                <Text style={{ fontSize: p.font(15) }}>⚡</Text>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }} numberOfLines={1}>
                    {s.name}
                  </Text>
                  {s.address ? (
                    <Text style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>
                      {s.address}
                    </Text>
                  ) : null}
                </View>
              </View>
            </View>
          ))}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, paddingLeft: 4 }}>
            <View style={{ width: 1, height: 16, backgroundColor: p.line }} />
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: p.green }} />
            <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }}>{card.destination}</Text>
          </View>
        </View>
      ) : (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <Text style={{ fontSize: p.font(22) }}>✅</Text>
          <View>
            <Text style={{ color: p.green, fontSize: p.font(14), fontWeight: '600' }}>全程无需补电</Text>
            <Text style={{ color: p.fg3, fontSize: p.font(11), marginTop: 2 }}>当前电量足以完成全程</Text>
          </View>
        </View>
      )}
    </CardShell>
  )
}

// 天色板与 HMI 同源（Cards.tsx:1131），同一份行程在两端不该是两套配色
const DAY_COLORS = ['#46D6E0', '#5B8CFF', '#9A6BFF', '#FF6BD6', '#34D399']
const TRIP_STOP_GLYPH: Record<string, string> = {
  attraction: '🏛',
  meal: '🍽',
  hotel: '🏨',
  charging: '⚡',
  custom: '📍',
}

/** 行程卡：结构化多日行程——按天列停靠点 + 段间补电。
 *  **只有 grounded 的停靠点才给「导航」按钮**：没接地的点连地址都还没定，
 *  给按钮等于承诺一个我们发不出去的指令。 */
export function TripItinerary({ p, card, onSend }: { p: Palette; card: TripItineraryCard; onSend: SendFn }) {
  const days = card.itinerary || []
  const [closed, setClosed] = useState<Record<number, boolean>>({}) // 缺省全展开，同 HMI
  const toggle = (d: number) => setClosed((prev) => ({ ...prev, [d]: !prev[d] }))
  return (
    <CardShell
      p={p}
      title={`${card.destination} · ${card.days}日行程`}
      right={
        <Chip
          p={p}
          text={card.status === 'confirmed' ? '已确认' : card.theme ? `《${card.theme}》主题` : '自驾 · AI 规划'}
        />
      }
    >
      {card.cities?.length && card.cities.length > 1 ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{card.cities.join(' → ')}</Text>
      ) : null}
      {days.map((day, di) => {
        const color = DAY_COLORS[di % DAY_COLORS.length]
        const charges = (day.legs || []).flatMap((l) => l.charging_stops || [])
        const isOpen = !closed[day.day_index]
        return (
          <View key={di} style={{ borderTopWidth: di ? 1 : 0, borderColor: p.line, paddingTop: di ? 8 : 0, gap: 6 }}>
            {charges.length ? (
              <View
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 8,
                  paddingHorizontal: 12,
                  paddingVertical: 6,
                  borderRadius: 10,
                  backgroundColor: p.amberSoft,
                }}
              >
                <Text style={{ fontSize: p.font(12) }}>⚡</Text>
                <Text style={{ color: p.amber, fontSize: p.font(11), flex: 1 }} numberOfLines={2}>
                  途中补电 {charges.length} 次：{charges.map((c) => c.name).join('、')}
                </Text>
              </View>
            ) : null}
            <Pressable
              onPress={() => toggle(day.day_index)}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}
            >
              <View
                style={{
                  width: 30,
                  height: 20,
                  borderRadius: 6,
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: `${color}20`,
                  borderWidth: 1,
                  borderColor: `${color}40`,
                }}
              >
                <Text style={{ color, fontSize: p.font(10), fontWeight: '700' }}>D{day.day_index}</Text>
              </View>
              <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600', flex: 1 }} numberOfLines={1}>
                {day.city ? `${day.city} · ` : ''}
                {day.theme || `第${day.day_index}天`}
              </Text>
              {day.weather?.text ? (
                <Text style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>
                  {day.weather.temp_low && day.weather.temp_high
                    ? `${day.weather.text} ${day.weather.temp_low}-${day.weather.temp_high}℃`
                    : day.weather.text}
                </Text>
              ) : null}
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{(day.stops || []).length}个点</Text>
              <Text style={{ color: p.fg3, fontSize: p.font(12) }}>{isOpen ? '▾' : '▸'}</Text>
            </Pressable>
            {isOpen
              ? (day.stops || []).map((s, i) => (
                  <View
                    key={i}
                    style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingLeft: 38, paddingVertical: 3 }}
                  >
                    <Text style={{ fontSize: p.font(13) }}>{TRIP_STOP_GLYPH[s.type] || '📍'}</Text>
                    <View style={{ flex: 1 }}>
                      <Text
                        style={{ color: s.grounded ? p.fg1 : p.fg2, fontSize: p.font(12) }}
                        numberOfLines={1}
                      >
                        {s.name}
                      </Text>
                      <Text style={{ color: p.fg3, fontSize: p.font(10) }} numberOfLines={1}>
                        {s.grounded ? s.poi?.address || '' : '待确认地点'}
                      </Text>
                    </View>
                    {s.grounded ? (
                      <Pressable
                        onPress={() => onSend(`导航去第${day.day_index}天的${s.name}`)}
                        style={{
                          paddingHorizontal: 10,
                          paddingVertical: 3,
                          borderRadius: 8,
                          backgroundColor: p.accentSoft,
                        }}
                      >
                        <Text style={{ color: p.accent, fontSize: p.font(11) }}>导航</Text>
                      </Pressable>
                    ) : null}
                  </View>
                ))
              : null}
          </View>
        )
      })}
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
        说「下一站」或「导航去第 2 天的XX」
      </Text>
    </CardShell>
  )
}
