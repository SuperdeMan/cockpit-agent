// 回执组件（方案 §5.3.2）：默认折叠成一行「已执行 · 展开回执」；展开四行。判据在 core/session/receipt.ts。
// 行车档「只播不展」留 B4。「安全检查」栏留位不渲染（Q16）。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

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

const MODE_LABEL: Record<InfoReceipt['mode'], string> = { real: '实时', cached: '缓存', degraded: '降级', mock: '模拟' }

export function ExecutionReceipt({ p, receipt }: { p: Palette; receipt: Receipt }) {
  const [open, setOpen] = useState(false)
  const head = receipt.kind === 'action' ? (receipt.executed.ok ? '已执行' : '执行失败') : '数据来源'
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
            <KV p={p} dense k="已理解" v={receipt.understood || receipt.executed.types.join('、')} />
            <KV p={p} dense k="目标" v={receipt.target} />
            <KV
              p={p}
              k="确认"
              v={receipt.confirm ? `你在手机端点了「${receipt.confirm.reply}」 ${hhmm(receipt.confirm.at)}` : '无需确认'}
            />
            <KV
              p={p}
              k="执行"
              v={`${receipt.executed.ok ? '成功' : '失败'}${receipt.executed.at ? ' · ' + hhmm(receipt.executed.at) : ''} · ${receipt.executed.types.join('、')}`}
            />
          </View>
        ) : (
          <View style={{ gap: 2, backgroundColor: p.surfaceLow, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
            <KV p={p} dense k="数据源" v={receipt.vendor || '未知'} />
            <KV p={p} dense k="更新" v={receipt.fetchedAt ? receipt.fetchedAt.slice(11, 16) : ''} />
            <KV p={p} dense k="定位" v={receipt.located ? '当前位置' : '未使用定位'} />
            <KV p={p} dense k="状态" v={`${MODE_LABEL[receipt.mode]}${receipt.note ? ' · ' + receipt.note : ''}`} />
          </View>
        )
      ) : null}
    </View>
  )
}
