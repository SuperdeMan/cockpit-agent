// 材质 spike 屏（B3-7 / 方案 §5.11、Q14）：真模糊要不要上，在这里裁——不通过就停在
// G1-tint，主线组件零依赖本屏。
//
// ⚠ **计划里写的三块对照结构在 SDK 57 上量不到真模糊**（本轮读 d.ts + BlurView.js 源码发现，
//    §6.2 出账）。两条事实：
//    ① `experimentalBlurMethod` 已 deprecated，正式 prop 是 `blurMethod`（前者仍可用但会 warn）；
//    ② Android 上给了 `blurMethod='dimezisBlurView'` 却**不给 `blurTarget`** 时，库
//       **静默回落成 'none'**（BlurView.js `_maybeWarnAboutBlurMethod` 原话：
//       "The blur view will fallback to \"none\" blur method to avoid errors"）——
//       屏上看不出区别，量到的是回落路径而不是真模糊。SDK 57 的真模糊接法是
//       `<BlurTargetView>` 包住要被糊的背景 + BlurView 的 `blurTarget` 指向它。
//
// 四块压在同一份高对比内容上（④ 是**仪器自检格**，不是凑数）：
//   ① G1-tint 现状（染色 + 边框，Glass 的等效参数）
//   ② BlurView 无 blurMethod（= 'none' 半透明回退）——对照组：证明 ②③ 的差异来自 blurMethod
//   ③ BlurView + blurMethod + blurTarget（真模糊路径）
//   ④ BlurView + blurMethod **无 blurTarget**（计划字面的写法）——若 ④ 与 ③ 长得一样，
//      说明 blurTarget 没起作用、③ 也没走真模糊；④≈② 而 ③ 不同才说明 ③ 量的是真模糊。
// 裁决三判据（计划 T9）：③ 糊层下文字边缘不可读（视觉）；framestats 60fps（性能，B2 G5 口径
// ——先读 24 列表头再解析）；③ 挂载/卸载 20 次不崩（稳定）。
import { BlurTargetView, BlurView } from 'expo-blur'
import { useEffect, useRef, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'
import { GLASS } from '@/ui/tokens'

/** 高对比背景条（糊不糊一眼可辨的判据物：细字 + 强色块交替） */
function Stripes({ fg, n = 6 }: { fg: string; n?: number }) {
  return (
    <View>
      {Array.from({ length: n }, (_, i) => (
        <View key={i} style={{ flexDirection: 'row', alignItems: 'center', height: 28, backgroundColor: i % 2 ? '#0a84ff' : '#111' }}>
          <Text style={{ color: fg, fontSize: 11 }} numberOfLines={1}>
            {i} 高对比判据行——真模糊下这行小字应不可读 abcdefg 0123456789
          </Text>
        </View>
      ))}
    </View>
  )
}

const BOX = { height: 168, borderRadius: 16, overflow: 'hidden' } as const
const FILL = { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 } as const

export default function BlurSpikeScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const [mountCount, setMountCount] = useState(0)
  const [blurOn, setBlurOn] = useState(true)
  // ③ 的 blurTarget：ref 要先挂上再渲 BlurView——BlurView 只在 componentDidMount 与
  // blurTarget.current 变化时读它，首帧 ref 还是 null 就会当成「没配」按 'none' 走。
  const targetRef = useRef<View | null>(null)
  const [ready, setReady] = useState(false)
  useEffect(() => setReady(true), [])

  const label = (t: string) => (
    <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{t}</Text>
  )

  return (
    <ScrollView style={{ flex: 1, backgroundColor: p.bg }} contentContainerStyle={{ padding: 12, gap: 12 }}>
      <Text style={{ color: p.fg1, fontSize: p.font(15) }}>
        ① G1-tint 现状 / ② BlurView 默认（回退=对照组）/ ③ blurMethod+blurTarget（真模糊）/ ④ blurMethod 无 target（自检）
      </Text>
      <Text testID="blur-target-state" style={{ color: p.fg3, fontSize: p.font(12) }}>
        blurTarget: {ready && targetRef.current ? 'attached' : 'null（③ 会静默回落成 none）'}
      </Text>

      {label('① G1-tint 现状（tint ' + GLASS.frosted.tint + ' / border ' + GLASS.frosted.border + '）')}
      <View style={BOX} testID="blur-tint">
        <Stripes fg="#fff" />
        <View style={{ ...FILL, backgroundColor: `rgba(10,14,24,${GLASS.frosted.tint})`, borderWidth: 1, borderColor: `rgba(255,255,255,${GLASS.frosted.border})` }} />
      </View>

      {label('② BlurView 无 blurMethod（Android = 半透明回退，对照组）')}
      <View style={BOX} testID="blur-default">
        <Stripes fg="#fff" />
        <BlurView intensity={60} tint="dark" style={FILL} />
      </View>

      {label('③ BlurView + blurMethod=dimezisBlurView + blurTarget（真模糊路径）')}
      <View style={BOX} testID="blur-real">
        <BlurTargetView ref={targetRef} style={FILL}>
          <Stripes fg="#fff" />
        </BlurTargetView>
        {ready && blurOn ? (
          <BlurView intensity={60} tint="dark" blurMethod="dimezisBlurView" blurTarget={targetRef} style={FILL} />
        ) : null}
      </View>

      {label('④ 同 ③ 但不给 blurTarget（= 计划字面写法）——仪器自检：④≈③ 说明 ③ 没糊')}
      <View style={BOX} testID="blur-notarget">
        <Stripes fg="#fff" />
        <BlurView intensity={60} tint="dark" blurMethod="dimezisBlurView" style={FILL} />
      </View>

      <Pressable
        testID="blur-remount"
        accessibilityRole="button"
        onPress={() => {
          setBlurOn((v) => !v)
          setMountCount((n) => n + 1)
        }}
        style={{ backgroundColor: p.fill, borderRadius: 12, padding: 12, minHeight: 44, justifyContent: 'center' }}
      >
        <Text style={{ color: p.fg1 }}>挂载/卸载 ③（稳定性判据：20 次不崩）· 已切 {mountCount} 次</Text>
      </Pressable>
    </ScrollView>
  )
}
