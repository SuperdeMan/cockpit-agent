// 回执组件（方案 §5.3.2）：默认折叠成一行「已执行 · 展开回执」；展开四行。判据在 core/session/receipt.ts。
// 行车档「只播不展」留 B4。「安全检查」栏留位不渲染（Q16）。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import { clockLabel, vendorName } from '@/core/cards/cardMeta'
import { STATUS_WORD } from '@/core/cards/controlResult'
import type { InfoReceipt, Receipt } from '@/core/session/receipt'
import { KV } from '@/features/cards/parts'
import { Icon, iconRuntimeAvailable } from '@/ui/Icon'
import type { Palette } from '@/ui/theme'
import { TARGET } from '@/ui/tokens'

function hhmm(ms: number | null): string {
  if (!ms) return ''
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// 与卡头 ProvBadge 同一套人话（部分数据 / 模拟数据），回执里不再另起「降级 / 模拟」
const MODE_LABEL: Record<InfoReceipt['mode'], string> = { real: '实时', cached: '缓存', degraded: '部分数据', mock: '模拟数据' }

export function ExecutionReceipt({ p, receipt }: { p: Palette; receipt: Receipt }) {
  const [open, setOpen] = useState(false)
  const head = receipt.kind === 'action' ? STATUS_WORD[receipt.executed.status] : '数据来源'
  return (
    <View style={{ gap: 4 }}>
      <Pressable
        testID="receipt-toggle"
        accessibilityRole="button"
        onPress={() => setOpen((o) => !o)}
        accessibilityState={{ expanded: open }}
        // 打磨批 A（评审 P14）44 → v3 补到目标高（48，大字 ×1.1）；字号 11 → 12（最小字号）
        style={{ minHeight: p.target(TARGET.parked), flexDirection: 'row', alignItems: 'center', gap: 4, alignSelf: 'flex-start' }}
      >
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>
          {head} · {open ? '收起回执' : '展开回执'}
        </Text>
        {iconRuntimeAvailable() ? <Icon name={open ? 'chevron-up' : 'chevron-down'} size={14} color={p.fg3} /> : null}
      </Pressable>
      {open ? (
        receipt.kind === 'action' ? (
          <View style={{ gap: 2, backgroundColor: p.surfaceLow, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
            <KV p={p} dense k="已理解" v={receipt.understood || receipt.executed.names.join('、')} />
            <KV p={p} dense k="目标" v={receipt.target} />
            <KV
              p={p}
              dense
              k="确认"
              v={receipt.confirm ? `你在手机端点了「${receipt.confirm.reply}」 ${hhmm(receipt.confirm.at)}` : '无需确认'}
            />
            <KV
              p={p}
              dense
              k="执行"
              v={[STATUS_WORD[receipt.executed.status], hhmm(receipt.executed.at), receipt.executed.names.join('、')].filter(Boolean).join(' · ')}
            />
          </View>
        ) : (
          <View style={{ gap: 2, backgroundColor: p.surfaceLow, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
            <KV p={p} dense k="数据源" v={vendorName(receipt.vendor) || '未知'} />
            {/* 原来切 ISO 第 11–16 位，那是 UTC 时刻（D10）；按本地时区读 */}
            <KV p={p} dense k="更新" v={clockLabel(receipt.fetchedAt)} />
            <KV p={p} dense k="定位" v={receipt.located ? '当前位置' : '未使用定位'} />
            <KV p={p} dense k="状态" v={`${MODE_LABEL[receipt.mode]}${receipt.note ? ' · ' + receipt.note : ''}`} />
          </View>
        )
      ) : null}
    </View>
  )
}
