// 出行族卡片（M1-4 首批）：poi_list（dest/waypoint 变体）/ poi_detail / place_list /
// place_detail / route_plan（estimate/cancelled 变体）。
// 行点按=合成与语音「第N个」同语义的一句话经普通 send（types.ts:52-58）；
// place 详情透传高德 POI id（不按名重搜取到别的分店——nearby 重构的老账）。
import { Pressable, Text, View } from 'react-native'

import type {
  PlaceDetailCard,
  PlaceListCard,
  PoiDetailCard,
  PoiListCard,
  RoutePlanCard,
} from '@shared/types.ts'

import type { Palette } from '../../ui/theme'
import { CardButtons, CardShell, Chip, KV, ProvBadge, type SendFn } from './parts'

function ItemRow({
  p,
  idx,
  title,
  sub,
  chips,
  onPress,
}: {
  p: Palette
  idx: number
  title: string
  sub?: string
  chips?: string[]
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
        <Text style={{ color: p.fg1, fontSize: p.font(14), fontWeight: '600' }} numberOfLines={1}>
          {title}
        </Text>
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
          onPress={() => onSend(`看${it.name}的详情`, it.id ? { nearby_poi_id: it.id } : undefined)}
        />
      ))}
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
