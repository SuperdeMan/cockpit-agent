// mobile/src/features/cards/ControlResult.tsx
// 车控结果卡（v3 P4c，D17；Figma Card/ControlResult · Card/ControlResult/Multi）：一项 = 单卡（对象 · 目标值 · 状态胶囊 · 证据行），
// 多项 = 清单卡（每项一行：图标 + 对象 + 目标值 + 状态）。行车档压成「标题 + 一行结果」。
// 状态与文案的判据在 core/cards/controlResult.ts，这里只画。卡内不放任何按钮：确认仍走全局 Dock（危险动作二次确认的唯一入口）。
import { Text, View } from 'react-native'

import { STATUS_WORD, itemName, overallStatus, type ControlItem, type ControlStatus } from '../../core/cards/controlResult'
import type { IconName } from '../../ui/Icon'
import type { Palette } from '../../ui/theme'
import { RADIUS } from '../../ui/tokens'
import { CardIcon, CardShell, cardText } from './parts'

/** 对象 → 图标（图标库里有的才配，其余用车） */
function objectIcon(item: ControlItem): IconName {
  const [head, mid] = item.command.split('.')
  if (head === 'hvac' || head === 'aircon') return 'snowflake'
  if (head === 'seat' && mid === 'heating') return 'seat-heat'
  if (head === 'window' || head === 'sunroof' || head === 'sunshade') return 'car-window'
  if (head === 'trunk') return 'trunk'
  if (head === 'door_lock') return item.command.endsWith('.open') ? 'unlock' : 'lock'
  if (head === 'ambient_light' || head === 'headlight' || head === 'accompany_home') return 'lightbulb'
  if (head === 'volume') return 'media'
  return 'vehicle'
}

/** 状态胶囊（Figma status：成功两态 surface/highest 底 + 绿字；本来就是 中性；未核实 琥珀；没生效 红；执行中 accent） */
const PILL: Readonly<Record<ControlStatus, { icon: IconName; tone: 'success' | 'neutral' | 'warning' | 'danger' | 'accent' }>> = {
  executed: { icon: 'check', tone: 'success' },
  verified: { icon: 'check-circle', tone: 'success' },
  unchanged: { icon: 'info', tone: 'neutral' },
  unverified: { icon: 'clock', tone: 'warning' },
  failed: { icon: 'warning', tone: 'danger' },
  running: { icon: 'refresh', tone: 'accent' },
}

function toneOf(p: Palette, tone: (typeof PILL)[ControlStatus]['tone']): { bg: string; fg: string } {
  if (tone === 'success') return { bg: p.surfaceHighest, fg: p.green }
  if (tone === 'warning') return { bg: p.amberSoft, fg: p.amber }
  if (tone === 'danger') return { bg: p.redSoft, fg: p.red }
  if (tone === 'accent') return { bg: p.accentSoft, fg: p.accent }
  return { bg: p.surfaceHighest, fg: p.fg2 }
}

function StatusPill({ p, status }: { p: Palette; status: ControlStatus }) {
  const { icon, tone } = PILL[status]
  const c = toneOf(p, tone)
  return (
    <View
      testID={`control-status-${status}`}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 4,
        backgroundColor: c.bg,
        borderRadius: RADIUS.full,
        paddingVertical: 3,
        paddingLeft: 8,
        paddingRight: 10,
      }}
    >
      <CardIcon p={p} name={icon} size={14} color={c.fg} />
      <Text style={[cardText(p, 'labelM'), { color: c.fg }]}>{STATUS_WORD[status]}</Text>
    </View>
  )
}

function hhmm(ms: number | null): string {
  if (!ms) return ''
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** 主文：有目标值给目标值（24°C）；没有就给动作的结果态（已打开 / 正在打开 / 打开） */
export function controlMainText(item: ControlItem): string {
  if (item.value) return item.value
  if (!item.action) return ''
  if (item.status === 'running') return `正在${item.action}`
  if (item.status === 'failed' || item.status === 'unchanged') return item.action
  return `已${item.action}`
}

/** 主文旁的说明（Figma value 行第二段）：「已设为」「执行前已经是这个温度」「目标温度」「正在设为」；没有目标值时不说 */
export function controlDescText(item: ControlItem): string {
  if (item.status === 'unchanged') return item.temperature ? '执行前已经是这个温度' : '执行前已经是这个状态'
  if (!item.value) return ''
  if (item.status === 'failed') return item.temperature ? '目标温度' : '目标值'
  if (!item.action) return ''
  return item.status === 'running' ? `正在${item.action}` : `已${item.action}`
}

/** 证据行（Figma 卡尾 caption）：未核实优先用服务端原话；时刻是这一轮 final 的本地时刻 */
export function controlFootText(item: ControlItem, at: number | null): string {
  const t = hhmm(at)
  switch (item.status) {
    case 'executed':
      return `${t ? `${t} · ` : ''}车辆已执行（本次未做状态核验）`
    case 'verified':
      return `车辆状态已确认${t ? ` · ${t}` : ''}`
    case 'unchanged':
      return '没有改动'
    case 'unverified':
      return item.note || '车辆状态还没核实'
    case 'failed':
      return '不过我没确认到这个操作真的生效，你留意一下。'
    case 'running':
      return '正在下发到车辆…'
  }
}

function footColor(p: Palette, status: ControlStatus): string {
  if (status === 'unverified') return p.amber
  if (status === 'failed') return p.red
  return p.fg3
}

function SingleResult({ p, item, at }: { p: Palette; item: ControlItem; at: number | null }) {
  const main = controlMainText(item)
  const desc = controlDescText(item)
  return (
    <CardShell
      p={p}
      gap={10}
      testID="control-result"
      icon={objectIcon(item)}
      title={`车控 · ${itemName(item)}`}
      right={<StatusPill p={p} status={item.status} />}
    >
      {main || desc ? (
        <View style={{ flexDirection: 'row', alignItems: 'baseline', flexWrap: 'wrap', columnGap: 10 }}>
          {main ? <Text style={[cardText(p, 'numericL'), { color: item.status === 'failed' ? p.fg3 : p.fg1 }]}>{main}</Text> : null}
          {desc ? <Text style={[cardText(p, 'bodyM'), { color: p.fg2 }]}>{desc}</Text> : null}
        </View>
      ) : null}
      <Text style={[cardText(p, 'caption'), { color: footColor(p, item.status) }]}>{controlFootText(item, at)}</Text>
    </CardShell>
  )
}

/** 清单卡卡头右槽：最需要留意的那类计数（没生效 > 未核实 > 已核实），都没有就不放 */
export function controlCountText(items: readonly ControlItem[]): string {
  for (const s of ['failed', 'unverified', 'verified'] as const) {
    const n = items.filter((i) => i.status === s).length
    if (n) return `${n} 项${STATUS_WORD[s]}`
  }
  return ''
}

function MultiResult({ p, items }: { p: Palette; items: readonly ControlItem[] }) {
  const count = controlCountText(items)
  return (
    <CardShell
      p={p}
      gap={4}
      testID="control-result"
      icon="vehicle"
      title={`车控 · ${items.length} 项`}
      right={count ? <Text style={[cardText(p, 'caption'), { color: p.fg3 }]}>{count}</Text> : undefined}
    >
      {items.map((item, i) => (
        <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 44 }}>
          <CardIcon p={p} name={objectIcon(item)} size={18} color={p.fg2} />
          <Text style={[cardText(p, 'bodyM'), { color: p.fg1, flex: 1 }]} numberOfLines={1}>
            {itemName(item)}
          </Text>
          {controlMainText(item) ? (
            <Text style={[cardText(p, 'numericM'), { color: item.status === 'failed' ? p.fg3 : p.fg1 }]}>{controlMainText(item)}</Text>
          ) : null}
          <StatusPill p={p} status={item.status} />
        </View>
      ))}
    </CardShell>
  )
}

/** 行车档（Figma Card/DrivingSummary · 车控）：标题 + 一行「目标值 / 动作 + 状态」，多项给总状态 + 对象清单 */
function DrivingResult({ p, items }: { p: Palette; items: readonly ControlItem[] }) {
  const one = items.length === 1 ? items[0] : null
  const status = overallStatus(items) ?? 'executed'
  const main = one ? `${one.value || one.action} ${STATUS_WORD[one.status]}`.trim() : STATUS_WORD[status]
  return (
    <CardShell
      p={p}
      gap={8}
      testID="control-result"
      icon={one ? objectIcon(one) : 'vehicle'}
      title={one ? `车控 · ${itemName(one)}` : `车控 · ${items.length} 项`}
    >
      <Text style={[cardText(p, 'numericL'), { color: one?.status === 'failed' ? p.red : p.fg1 }]} numberOfLines={1}>
        {main}
      </Text>
      {one ? null : (
        <Text style={[cardText(p, 'bodyM'), { color: p.fg2 }]} numberOfLines={1}>
          {items.map(itemName).join('、')}
        </Text>
      )}
    </CardShell>
  )
}

export function ControlResult({
  p,
  items,
  at = null,
  driving = false,
}: {
  p: Palette
  items: readonly ControlItem[]
  at?: number | null
  driving?: boolean
}) {
  if (!items.length) return null
  if (driving) return <DrivingResult p={p} items={items} />
  return items.length === 1 ? <SingleResult p={p} item={items[0]} at={at} /> : <MultiResult p={p} items={items} />
}
