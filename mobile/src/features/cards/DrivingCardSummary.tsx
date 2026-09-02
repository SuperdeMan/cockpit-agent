// mobile/src/features/cards/DrivingCardSummary.tsx
// 行车压缩卡（B4-11 / 方案 §6「一屏只有一张卡、只显示标题 + ≤2 个字段 + 1 个主按钮」）。
// **不改 34 个渲染器**：从任意卡里探取（core/cards/cardFields.ts），card_group 取主卡
// （display_priority 的判据在 cardGroup.ts，这里不判）。只在行车档的语音层里用——
// 记录里的卡照旧全量渲（记录在常驻层身后）。目标 56dp（TARGET.driving）。
import { Pressable, Text } from 'react-native'

import { cardListRows, cardPrimaryButton, cardPrimaryFields } from '@/core/cards/cardFields'
import { splitCardGroup } from '@/core/cards/cardGroup'
import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

import { CardShell, ProvBadge, type SendFn } from './parts'

/* eslint-disable @typescript-eslint/no-explicit-any */
export function DrivingCardSummary({
  p,
  fontScale,
  card,
  onSend,
}: {
  p: Palette
  fontScale: FontScalePref
  card: any
  onSend: SendFn
}) {
  const main = card?.type === 'card_group' ? splitCardGroup((card.items as any[]) || []).main : card
  if (!main || typeof main !== 'object') return null
  const fields = cardPrimaryFields(main, 3) // 首个当标题，其余最多两个字段
  const rows = cardListRows(main, 1)
  const title = fields[0]?.[1] ?? rows[0]?.title ?? String(main.type || '')
  const kv = fields.slice(1, 3)
  const button = cardPrimaryButton(main)
  const h = scale(TARGET.driving, 'target', fontScale)
  const body = scale(TYPE.body, 'text', fontScale)
  return (
    <CardShell p={p} title={String(main.type || '')} right={<ProvBadge p={p} prov={main._prov} />}>
      <Text
        testID="driving-card-title"
        numberOfLines={1}
        style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }}
      >
        {title}
      </Text>
      {rows[0]?.sub ? (
        <Text numberOfLines={1} style={{ color: p.fg2, fontSize: body }}>
          {rows[0].sub}
        </Text>
      ) : null}
      {kv.map(([k, v]) => (
        <Text key={k} numberOfLines={1} style={{ color: p.fg2, fontSize: body }}>
          {k}: {v}
        </Text>
      ))}
      {button ? (
        <Pressable
          testID="driving-card-button"
          accessibilityRole="button"
          accessibilityLabel={button.label}
          onPress={() => onSend(button.send_text)}
          style={{
            minHeight: h,
            borderRadius: RADIUS.md,
            backgroundColor: p.accentSoft,
            borderWidth: 1,
            borderColor: p.accent,
            alignItems: 'center',
            justifyContent: 'center',
            paddingHorizontal: 16,
          }}
        >
          <Text style={{ color: p.accent, fontSize: scale(TYPE.h2, 'text', fontScale) }}>{button.label}</Text>
        </Pressable>
      ) : null}
    </CardShell>
  )
}
