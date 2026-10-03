// 卡片公共件（实施计划 M1-4；Android Visual v3 P4a 重做，Figma Card/Shell · Header · KVRow · Actions · ProvBadge ·
// FreshChip · ConfBadge）：外框 / 卡头 / 键值行 / chip / 动作区 / _prov 角标 / 时效 / 置信。
// 按钮语义（types.ts:52-58）：卡内动作只合成一句自然语言经普通 send 上行，不是业务写接口。
// 人话判据（厂商中文名、本地时刻、置信档位）在 core/cards/cardMeta.ts，这里只消费。
import type { ReactNode } from 'react'
import { Text, View, type TextStyle } from 'react-native'

import type { CardButton, Provenance } from '@shared/types.ts'

import { CONF_LABEL, clockLabel, confLevel, vendorName } from '../../core/cards/cardMeta'
import { Button } from '../../ui/Button'
import { Icon, iconRuntimeAvailable, type IconName } from '../../ui/Icon'
import type { Palette } from '../../ui/theme'
import { RADIUS, textStyle, type TextRole } from '../../ui/tokens'

export type SendFn = (text: string, metaExtra?: Record<string, string>) => void

/** 卡片里的字阶：卡片渲染器只拿得到 Palette，字号档位从 `p.fontScale` 读（与页面同一份 TEXT 表） */
export function cardText(p: Palette, role: TextRole): TextStyle {
  return textStyle(role, p.fontScale)
}

/** 卡片外壳 + 卡头（Figma Card/Shell：surface + 一级投影、圆角 16、内边距 16、件间 12；
 *  Card/Header：图标 20 + 「类别 · 实体」titleM（长标题折两行，如「欧冠联赛 · Real Madrid vs Manchester City」）
 *  + 右槽——来源·时效 / _prov 角标 / 置信 / 无）。每种卡都有图标 */
export function CardShell({
  p,
  icon,
  title,
  right,
  children,
  testID,
  gap = 12,
}: {
  p: Palette
  icon?: IconName
  title?: string
  right?: ReactNode
  children: ReactNode
  testID?: string
  /** 件间距：卡片默认 12；车控结果单卡 10、清单卡 4、行车摘要 8（Figma 各组件自己的 itemSpacing） */
  gap?: number
}) {
  return (
    <View
      testID={testID}
      style={{
        backgroundColor: p.surface,
        borderWidth: 1,
        borderColor: p.line,
        borderRadius: RADIUS.lg,
        padding: 16,
        gap,
        boxShadow: p.elev1,
      }}
    >
      {title || right ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {icon ? <CardIcon p={p} name={icon} size={20} color={p.fg2} /> : null}
          <Text style={[cardText(p, 'titleM'), { color: p.fg1, flex: 1 }]} numberOfLines={2}>
            {title ?? ''}
          </Text>
          {right}
        </View>
      ) : null}
      {children}
    </View>
  )
}

/** 键值行（Figma Card/KVRow：最小高 32，键 bodyM 次级色、值 bodyM 主色右对齐）。
 *  `dense`：回执那类紧凑清单（caption 键值、左对齐，R-3 回执行 16 高） */
export function KV({ p, k, v, dense = false }: { p: Palette; k: string; v?: string | number | null; dense?: boolean }) {
  if (v === undefined || v === null || v === '') return null
  if (dense) {
    return (
      <View style={{ flexDirection: 'row', gap: 12 }}>
        <Text style={[cardText(p, 'caption'), { color: p.fg3, minWidth: 56 }]}>{k}</Text>
        <Text style={[cardText(p, 'caption'), { color: p.fg2, flex: 1 }]}>{String(v)}</Text>
      </View>
    )
  }
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 32 }}>
      <Text style={[cardText(p, 'bodyM'), { color: p.fg2 }]}>{k}</Text>
      <Text style={[cardText(p, 'bodyM'), { color: p.fg1, flex: 1, textAlign: 'right' }]}>{String(v)}</Text>
    </View>
  )
}

export function Chip({ p, text, tone = 'plain' }: { p: Palette; text: string; tone?: 'plain' | 'accent' | 'amber' }) {
  const bg = tone === 'accent' ? p.accentSoft : tone === 'amber' ? p.amberSoft : 'transparent'
  const fg = tone === 'accent' ? p.accent : tone === 'amber' ? p.amber : p.fg2
  return (
    <View
      style={{
        backgroundColor: bg,
        borderColor: tone === 'plain' ? p.line : 'transparent',
        borderWidth: tone === 'plain' ? 1 : 0,
        borderRadius: RADIUS.full,
        paddingHorizontal: 8,
        paddingVertical: 2,
      }}
    >
      <Text style={[cardText(p, 'caption'), { color: fg }]}>{text}</Text>
    </View>
  )
}

/** 卡尾动作（Figma Card/Actions）：一律 send_text 经普通 send 上行（危险动作仍由全局确认条二次确认，卡内不放确认键）。
 *  主动作 Tonal、其余 Outlined；一个 = 整宽，两个 = 各半，更多两列折行。高 = 目标高（Button 原语） */
export function CardButtons({
  p,
  buttons,
  onSend,
}: {
  p: Palette
  buttons?: (CardButton | { label?: string; send_text?: string })[]
  onSend: SendFn
}) {
  const usable = (buttons || []).filter((b) => b?.label && b?.send_text)
  if (!usable.length) return null
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {usable.map((b, i) => (
        <Button
          key={i}
          p={p}
          // B4-9：TalkBack 读「按钮 + 标签」（§8「卡片按钮补 role/label」）——Button 原语自带 role 与说明
          label={b.label!}
          accessibilityLabel={b.label}
          variant={i === 0 ? 'tonal' : 'outlined'}
          fontScale={p.fontScale}
          onPress={() => onSend(b.send_text!)}
          style={{ flexGrow: 1, flexBasis: usable.length === 1 ? '100%' : '45%' }}
        />
      ))}
    </View>
  )
}

/**
 * 时效相对化（M3-1）：`2026-08-25T01:56:00.000Z` → 「3小时前」。
 *
 * ⚠ **这是 `hmi/src/components/Cards.tsx:592 relativeTime` 的第二份实现**，明说不藏：
 * 那份住在 `.tsx` 组件文件里、不是共享模块，搬出来要动 hmi 的 5 个调用点，
 * 而「hmi 一行不改」是本计划的边界（§10）——它的例外是「共享模块**有 bug**」，
 * 而这里 hmi 是对的，错的是 App 直显了 ISO 原文。⇒ 在边界内修，把重复记在这。
 * **判据逐行照抄那份**（阈值 60s/60min/24h/30d、mock 判空、NaN 判空），
 * 保持两端对同一张卡说同一句话。要收敛就得先把它提成共享模块 + node:test + 台账，
 * 那是一次独立的共享面改动，已挂账给泓舟裁。
 * v3 起它只给「缓存 · N分钟前」与调研卡的时效用；来源角标与时效标签走 cardMeta.clockLabel（本地时刻）。
 */
export function relativeTime(iso?: string): string {
  if (!iso || iso === 'mock') return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 60000) return '刚刚'
  const min = Math.floor(diff / 60000)
  if (min < 60) return `${min}分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day}天前`
  return new Date(t).toLocaleDateString('zh-CN')
}

/** 卡片族的线性图标（打磨批 C，评审 P15）：emoji 不再当图标。svg 原生缺席时回退一枚同色圆点——
 *  iconRuntimeAvailable 是既有判据（Icon.tsx / 坑账 §9.27），这里只消费。 */
export function CardIcon({ p, name, size = 16, color }: { p: Palette; name: IconName; size?: number; color?: string }) {
  const c = color ?? p.fg2
  if (iconRuntimeAvailable()) return <Icon name={name} size={size} color={c} />
  return <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}><View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: c }} /></View>
}

/** 时效（Figma Card/FreshChip）：时钟 + 「12:51 更新」，跨天「昨天 23:40 更新 / 9月21日 更新」。
 *  解析不出时刻（缺失 / mock / 坏串）就整个不渲染，不留一个空标签 */
export function FreshChip({ p, iso }: { p: Palette; iso?: string }) {
  const label = clockLabel(iso)
  if (!label) return null
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      {iconRuntimeAvailable() ? <Icon name="clock" size={12} color={p.fg3} /> : null}
      <Text style={[cardText(p, 'caption'), { color: p.fg3 }]}>{label} 更新</Text>
    </View>
  )
}

/** 卡内标签（Figma tag：surface/highest 胶囊 + caption 次级色——点球 / 赛季 / 缓存；amber 档给模拟数据、部分数据） */
export function Badge({ p, text, tone }: { p: Palette; text: string; tone: 'amber' | 'neutral' }) {
  return (
    <View
      style={{
        backgroundColor: tone === 'amber' ? p.amberSoft : p.surfaceHighest,
        borderRadius: RADIUS.full,
        paddingHorizontal: 8,
        paddingVertical: 2,
      }}
    >
      <Text style={[cardText(p, 'caption'), { color: tone === 'amber' ? p.amber : p.fg2 }]} numberOfLines={1}>
        {text}
      </Text>
    </View>
  )
}

/** 数据真实性（契约 §9.3 四态；Figma Card/ProvBadge）：mock = 「模拟数据」、degraded = 「部分数据」（琥珀胶囊，必须醒目，坑账 #6）；
 *  cached = 「缓存 · N分钟前」；real 不出胶囊，只在右槽给一行「来源 · 本地时刻」（厂商中文名，例「和风 · 12:51」），
 *  来源自身带日期的（手册）给「手册版本 2024-04-15」 */
export function ProvBadge({ p, prov }: { p: Palette; prov?: Provenance }) {
  if (!prov?.mode) return null
  if (prov.mode === 'mock') return <Badge p={p} tone="amber" text="模拟数据" />
  if (prov.mode === 'degraded') return <Badge p={p} tone="amber" text="部分数据" />
  if (prov.mode === 'cached') {
    const age = relativeTime(prov.fetched_at) || prov.note || ''
    return <Badge p={p} tone="neutral" text={age ? `缓存 · ${age}` : '缓存'} />
  }
  // 来源自身带日期（手册版本等）⇒「手册版本 2024-04-15」：这类来源的 vendor 是文档 id，不是厂商（D19）
  const label = prov.data_time
    ? `${prov.data_time_label || '数据日期'} ${prov.data_time}`
    : [vendorName(prov.vendor), clockLabel(prov.fetched_at)].filter(Boolean).join(' · ')
  return label ? (
    <Text style={[cardText(p, 'caption'), { color: p.fg3 }]} numberOfLines={1}>
      {label}
    </Text>
  ) : null
}

/** 置信（Figma Card/ConfBadge）：圆点 + 人话（可信度高 / 可信度中 / 未充分核实）；契约外的值不画 */
export function ConfBadge({ p, level }: { p: Palette; level?: string }) {
  const lv = confLevel(level)
  if (!lv) return null
  const color = lv === 'high' ? p.green : lv === 'low' ? p.amber : p.fg2
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color }} />
      <Text style={[cardText(p, 'caption'), { color }]}>{CONF_LABEL[lv]}</Text>
    </View>
  )
}
