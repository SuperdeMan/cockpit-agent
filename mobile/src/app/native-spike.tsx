// B3 原生件取证屏（照 M2 /voice-spike 先例）。§11.2 B3 的两条验收在这里读：
//  ① 折叠姿态：Fold 4 真机手折半开，「posture」行要变 tabletop（铰链横）——
//     cmd device_state 模拟不了半开（B2 §6.4 的 device_state 口径只有 0/1/2/3 档），要人手。
//  ② 触感四种：四个按钮各触发一次（§8：唤醒轻/确认双/判死一/快门轻）；
//     自然挂点的代表性验证另有两条（计划 T9——按钮验的是「振感对不对」，挂点验的是「时机对不对」）。
import { useEffect, useState } from 'react'
import { PixelRatio, Pressable, ScrollView, Text, useWindowDimensions } from 'react-native'
import { useStore } from 'zustand'

import { HAPTIC_KINDS, performHaptic } from '@/core/haptics'
import { drivingActive, NO_EDGE_DRIVING, type DrivingEdgeFact } from '@/core/presence/drivingMode'
import { getWired } from '@/core/session/wiring'
import { settingsStore } from '@/core/settings/store'
import { playCueTone } from '@/core/voice/cueTone'
import { foldPosture } from '@/ui/layout/foldPosture'
import { useFoldState } from '@/ui/layout/useFoldState'
import { useLayout } from '@/ui/layout/useLayout'
import { usePalette } from '@/ui/theme'

import FoldNative, { FOLD_NATIVE_AVAILABLE } from '../../modules/foldstate'

export default function NativeSpikeScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const fold = useFoldState()
  // B4-6 形态矩阵截图的机器读数：布局模式 / 尺寸类 / 实测 dp / 铰链 dp——不靠肉眼数屏数。
  // ⛔ B4-13 缺陷 B：这一行原来写死 `useLayout(false)`（T6 加它时 T11 的行车档还没接上），
  //    于是行车档下它必然显示错的 mode（实测行车 + expanded×compact 显示 single，真实应是 driving-landscape）。
  //    改成读**真实**行车事实：判据仍是 drivingMode.drivingActive（唯一一份），本屏只搬事实。
  const [edge, setEdge] = useState<DrivingEdgeFact>(() => getWired()?.core.store.getState().drivingEdge ?? NO_EDGE_DRIVING)
  // B5-3 缺陷 C：用户退出只压本段，取证屏也要看得见它（写死 0 就是 B4 缺陷 B 的同款说谎）
  const [dismissedAt, setDismissedAt] = useState<number>(() => getWired()?.core.store.getState().drivingDismissedAt ?? 0)
  useEffect(() => {
    const core = getWired()?.core
    if (!core) return
    setEdge(core.store.getState().drivingEdge) // 挂载与订阅之间的缝
    setDismissedAt(core.store.getState().drivingDismissedAt)
    return core.store.subscribe((st) => {
      setEdge(st.drivingEdge)
      setDismissedAt(st.drivingDismissedAt)
    })
  }, [])
  // ⚠ 事件驱动，**不加 1s ticker**：取证屏加轮询会让 `uiautomator dump` 拿不到 idle（§6.2 补取轮坑⑨）。
  //    代价是 30s 退出宽限的那一跳不会自己刷新——所以下面把 edge 的两个时刻一起打出来，读的人看得见。
  const driving = drivingActive({ manual: settings.drivingManual, edge, now: Date.now(), dismissedAt })
  const layout = useLayout(driving)
  const { width, height } = useWindowDimensions()
  const [events, setEvents] = useState(0)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', () => setEvents((n) => n + 1))
    return () => sub.remove()
  }, [])

  // B5-6：原生缓存的最近一次投影。新挂载实例的初值来自它（不是新事件）——所以「posture 有值 + events: 0」才是对的读数。
  const currentNative = FoldNative?.current?.() ?? null

  const rows: Array<[string, string]> = [
    ['native', FOLD_NATIVE_AVAILABLE ? 'available' : 'MISSING（旧 APK？重建没带上？）'],
    ['posture', foldPosture(fold)],
    ['state', fold?.state ?? '—'],
    ['orientation', fold?.orientation ?? '—'],
    ['isSeparating', String(fold?.isSeparating ?? '—')],
    ['bounds', fold?.bounds ? JSON.stringify(fold.bounds) : '—'],
    ['events', String(events)],
    ['current', currentNative ? JSON.stringify(currentNative) : '—（旧 APK 无 current / 从未收到事件）'],
    ['driving', `${driving}（manual=${settings.drivingManual} edge.trueAt=${edge.trueAt} edge.falseAt=${edge.falseAt} dismissedAt=${dismissedAt}）`],
    ['layout', `${layout.mode} · ${layout.widthClass}×${layout.heightClass}`],
    ['dp', `${Math.round(width)}×${Math.round(height)} @${PixelRatio.get()}x`],
    ['hinge(dp)', layout.hinge ? JSON.stringify(layout.hinge) : '—'],
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
