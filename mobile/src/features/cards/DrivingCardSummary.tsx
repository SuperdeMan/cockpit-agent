// mobile/src/features/cards/DrivingCardSummary.tsx
// 行车压缩卡（B4-11 / 方案 §6「一屏只有一张卡、只显示标题 + ≤2 个字段 + 1 个主按钮」；v3 P4d 按卡型模板，差异 D11）。
// 模板在 core/cards/drivingSummary.ts（标题 / 主数值 / 副行 / 主按钮按卡型取，card_group 取主卡）；这里只画
// Figma Card/DrivingSummary：卡头图标 + title/l、主数值 numeric/l、副行 body/m 次级色、一个实心主按钮（行车 56）。
// 车控结果卡的行车形态也用这个视图（ControlResult）。只在行车档的语音层里用——记录里的卡照旧全量渲。
import { Text, View } from 'react-native'

import { drivingSummary, type DrivingSummary } from '@/core/cards/drivingSummary'
import type { FontScalePref } from '@/core/settings/store'
import { Button } from '@/ui/Button'
import type { Palette } from '@/ui/theme'
import { RADIUS, textStyle } from '@/ui/tokens'

import { CardIcon, type SendFn } from './parts'

export function DrivingSummaryView({
  p,
  summary,
  onSend,
  testID = 'driving-card',
}: {
  p: Palette
  summary: DrivingSummary
  onSend?: SendFn
  testID?: string
}) {
  const main = summary.tone === 'up' ? p.dataUp : summary.tone === 'down' ? p.dataDown : summary.tone === 'warn' ? p.amber : p.fg1
  const button = summary.button
  return (
    <View
      testID={testID}
      style={{
        backgroundColor: p.surface,
        borderWidth: 1,
        borderColor: p.line,
        borderRadius: RADIUS.lg,
        padding: 16,
        gap: 8,
        boxShadow: p.elev1,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <CardIcon p={p} name={summary.icon} size={20} color={p.fg2} />
        <Text testID="driving-card-head" numberOfLines={1} style={[textStyle('titleL', p.fontScale), { color: p.fg1, flex: 1 }]}>
          {summary.title}
        </Text>
      </View>
      {summary.main ? (
        <Text testID="driving-card-title" numberOfLines={1} style={[textStyle('numericL', p.fontScale), { color: main }]}>
          {summary.main}
        </Text>
      ) : null}
      {summary.sub ? (
        <Text numberOfLines={1} style={[textStyle('bodyM', p.fontScale), { color: summary.subWarn ? p.amber : p.fg2 }]}>
          {summary.sub}
        </Text>
      ) : null}
      {button && onSend ? (
        <Button
          p={p}
          testID="driving-card-button"
          label={button.label}
          accessibilityLabel={button.label}
          variant="filled"
          driving
          fontScale={p.fontScale}
          onPress={() => onSend(button.send_text)}
          style={{ alignSelf: 'stretch' }}
        />
      ) : null}
    </View>
  )
}

export function DrivingCardSummary({
  p,
  card,
  onSend,
}: {
  p: Palette
  /** 字号档已在 Palette.fontScale 里；参数留着与调用方兼容 */
  fontScale?: FontScalePref
  card: unknown
  onSend: SendFn
}) {
  const summary = drivingSummary(card)
  return summary ? <DrivingSummaryView p={p} summary={summary} onSend={onSend} /> : null
}
