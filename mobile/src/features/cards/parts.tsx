// 卡片公共件（实施计划 M1-4）：外框 / 键值行 / chip / 按钮排 / _prov 徽章。
// 按钮语义（types.ts:52-58）：卡内动作只合成一句自然语言经普通 send 上行，不是业务写接口。
import type { ReactNode } from 'react'
import { Pressable, Text, View } from 'react-native'

import type { CardButton, Provenance } from '@shared/types.ts'

import type { Palette } from '../../ui/theme'

export type SendFn = (text: string, metaExtra?: Record<string, string>) => void

export function CardShell({
  p,
  title,
  right,
  children,
}: {
  p: Palette
  title?: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <View
      style={{
        backgroundColor: p.card,
        borderColor: p.line,
        borderWidth: 1,
        borderRadius: 14,
        padding: 12,
        gap: 8,
      }}
    >
      {(title || right) && (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          {title ? (
            <Text style={{ color: p.fg2, fontSize: p.font(12), fontWeight: '600' }} numberOfLines={1}>
              {title}
            </Text>
          ) : (
            <View />
          )}
          {right}
        </View>
      )}
      {children}
    </View>
  )
}

export function KV({ p, k, v }: { p: Palette; k: string; v?: string | number | null }) {
  if (v === undefined || v === null || v === '') return null
  return (
    <View style={{ flexDirection: 'row', gap: 8 }}>
      <Text style={{ color: p.fg3, fontSize: p.font(12), minWidth: 56 }}>{k}</Text>
      <Text style={{ color: p.fg2, fontSize: p.font(12), flex: 1 }}>{String(v)}</Text>
    </View>
  )
}

export function Chip({ p, text, tone = 'plain' }: { p: Palette; text: string; tone?: 'plain' | 'accent' | 'amber' }) {
  const bg = tone === 'accent' ? p.accentSoft : tone === 'amber' ? p.amberSoft : 'transparent'
  const fg = tone === 'accent' ? p.accent : tone === 'amber' ? p.amber : p.fg3
  return (
    <View
      style={{
        backgroundColor: bg,
        borderColor: tone === 'plain' ? p.line : 'transparent',
        borderWidth: tone === 'plain' ? 1 : 0,
        borderRadius: 999,
        paddingHorizontal: 8,
        paddingVertical: 2,
      }}
    >
      <Text style={{ color: fg, fontSize: p.font(11) }}>{text}</Text>
    </View>
  )
}

/** 卡内按钮排：一律 send_text 经普通 send 上行（危险动作仍由全局确认条二次确认） */
export function CardButtons({
  p,
  buttons,
  onSend,
}: {
  p: Palette
  buttons?: Array<CardButton | { label?: string; send_text?: string }>
  onSend: SendFn
}) {
  const usable = (buttons || []).filter((b) => b?.label && b?.send_text)
  if (!usable.length) return null
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 2 }}>
      {usable.map((b, i) => (
        <Pressable
          key={i}
          onPress={() => onSend(b.send_text!)}
          style={{
            backgroundColor: p.accentSoft,
            borderRadius: 10,
            paddingHorizontal: 12,
            minHeight: 36,
            justifyContent: 'center',
          }}
        >
          <Text style={{ color: p.accent, fontSize: p.font(13) }}>{b.label}</Text>
        </Pressable>
      ))}
    </View>
  )
}

/** 数据真实性徽章（契约 §9.3 四态）：mock 必须醒目（坑账 #6），real 只角标来源·时间 */
export function ProvBadge({ p, prov }: { p: Palette; prov?: Provenance }) {
  if (!prov?.mode) return null
  if (prov.mode === 'mock') {
    return <Chip p={p} tone="amber" text="⚠ 模拟数据" />
  }
  if (prov.mode === 'degraded') {
    return <Chip p={p} tone="amber" text={`降级${prov.note ? ` · ${prov.note}` : ''}`} />
  }
  if (prov.mode === 'cached') {
    return <Chip p={p} text={`缓存${prov.note ? ` · ${prov.note}` : ''}`} />
  }
  const label = [prov.vendor, prov.fetched_at?.slice(11, 16)].filter(Boolean).join(' · ')
  return label ? <Chip p={p} text={label} /> : null
}
