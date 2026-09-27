import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'
import type { Msg } from '@shared/types.ts'
import { resultDetails } from '@shared/resultBundle.mjs'
import type { Palette } from '../../ui/theme'
import { TARGET } from '../../ui/tokens'
import { CardRenderer } from '../cards/CardRenderer'
import type { SendFn } from '../cards/parts'

export function ResultDetailsFold({ p, msg, driving, onSend }: { p: Palette; msg: Msg; driving: boolean; onSend: SendFn }) {
  const [open, setOpen] = useState(false)
  const rows = resultDetails(msg)
  if (driving || msg.driving || msg.pending || !rows.length) return null
  return (
    <View style={{ borderTopWidth: 1, borderTopColor: p.fill2, marginTop: 4 }}>
      <Pressable
        testID="result-details-toggle"
        accessibilityRole="button"
        accessibilityLabel="查看各项结果"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen(!open)}
        style={{ minHeight: p.target(TARGET.parked), justifyContent: 'center' }}
      >
        <Text style={{ color: p.accent, fontSize: p.font(13) }}>查看各项结果 {open ? '−' : '+'}</Text>
      </Pressable>
      {open ? <View testID="result-details" style={{ gap: 16, paddingBottom: 4 }}>
        {rows.map((row, index) => <View key={row.key} style={{ gap: 8 }}>
          {rows.length > 1 ? <Text style={{ color: p.fg3, fontSize: p.font(12) }}>结果 {index + 1}</Text> : null}
          {row.answer ? <Text selectable style={{ color: p.fg1, fontSize: p.font(15), lineHeight: p.font(23) }}>{row.answer}</Text> : null}
          {row.card ? <CardRenderer p={p} card={row.card} onSend={onSend} /> : null}
        </View>)}
      </View> : null}
    </View>
  )
}
