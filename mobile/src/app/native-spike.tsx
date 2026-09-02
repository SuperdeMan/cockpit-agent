// B3 原生件取证屏（照 M2 /voice-spike 先例）。§11.2 B3 的两条验收在这里读：
//  ① 折叠姿态：Fold 4 真机手折半开，「posture」行要变 tabletop（铰链横）——
//     cmd device_state 模拟不了半开（B2 §6.4 的 device_state 口径只有 0/1/2/3 档），要人手。
//  ② 触感四种：四个按钮各触发一次（§8：唤醒轻/确认双/判死一/快门轻）；
//     自然挂点的代表性验证另有两条（计划 T9——按钮验的是「振感对不对」，挂点验的是「时机对不对」）。
import { useEffect, useState } from 'react'
import { Pressable, ScrollView, Text } from 'react-native'
import { useStore } from 'zustand'

import { HAPTIC_KINDS, performHaptic } from '@/core/haptics'
import { settingsStore } from '@/core/settings/store'
import { playCueTone } from '@/core/voice/cueTone'
import { foldPosture } from '@/ui/layout/foldPosture'
import { useFoldState } from '@/ui/layout/useFoldState'
import { usePalette } from '@/ui/theme'

import FoldNative, { FOLD_NATIVE_AVAILABLE } from '../../modules/foldstate'

export default function NativeSpikeScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const fold = useFoldState()
  const [events, setEvents] = useState(0)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', () => setEvents((n) => n + 1))
    return () => sub.remove()
  }, [])

  const rows: Array<[string, string]> = [
    ['native', FOLD_NATIVE_AVAILABLE ? 'available' : 'MISSING（旧 APK？重建没带上？）'],
    ['posture', foldPosture(fold)],
    ['state', fold?.state ?? '—'],
    ['orientation', fold?.orientation ?? '—'],
    ['isSeparating', String(fold?.isSeparating ?? '—')],
    ['bounds', fold?.bounds ? JSON.stringify(fold.bounds) : '—'],
    ['events', String(events)],
  ]

  return (
    <ScrollView style={{ flex: 1, backgroundColor: p.bg }} contentContainerStyle={{ padding: 16, gap: 8 }}>
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600' }}>B3 原生件取证</Text>
      {rows.map(([k, v]) => (
        <Text key={k} testID={`fold-${k}`} style={{ color: p.fg2, fontSize: p.font(14) }}>
          {k}: {v}
        </Text>
      ))}
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600', marginTop: 16 }}>触感四种（§8）</Text>
      {HAPTIC_KINDS.map((kind) => (
        <Pressable
          key={kind}
          testID={`haptic-${kind}`}
          accessibilityRole="button"
          onPress={() => performHaptic(kind)}
          style={{ backgroundColor: p.fill, borderRadius: 12, padding: 12, minHeight: 44, justifyContent: 'center' }}
        >
          <Text style={{ color: p.fg1, fontSize: p.font(14) }}>
            {kind}（{kind === 'wake' ? '唤醒·轻' : kind === 'confirm' ? '确认·双' : kind === 'dead' ? '判死·一记重' : '快门·轻'}）
          </Text>
        </Pressable>
      ))}
      <Text style={{ color: p.fg1, fontSize: p.font(16), fontWeight: '600', marginTop: 16 }}>提示音两种（§8，B4）</Text>
      {(['wake', 'attention'] as const).map((kind) => (
        <Pressable
          key={kind}
          testID={`cue-${kind}`}
          accessibilityRole="button"
          onPress={() => playCueTone(kind)}
          style={{ backgroundColor: p.fill, borderRadius: 12, padding: 12, minHeight: 44, justifyContent: 'center' }}
        >
          <Text style={{ color: p.fg1, fontSize: p.font(14) }}>{kind}（{kind === 'wake' ? '唤醒 · 两音上行' : '需确认 · 两音下行'}）</Text>
        </Pressable>
      ))}
    </ScrollView>
  )
}
