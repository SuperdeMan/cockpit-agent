// 状态画廊（UX v2.1 §11.2 B1 验收 + §11.4 可读性判据的取数屏）。与 card-gallery 同类：
// dev 取证入口，不进主链路；`?only=` 直达；每条标「真栈可产 / 仅样本」。
import { useLocalSearchParams } from 'expo-router'
import { useMemo } from 'react'
import { ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { useReduceMotion } from '@/core/a11y/reduceMotion'
import { presenceFixtures } from '@/core/presence/fixtures'
import { settingsStore } from '@/core/settings/store'
import { Chip } from '@/features/cards/parts'
import { FocusDock } from '@/features/chat/FocusDock'
import { PresenceCapsule } from '@/features/chat/PresenceCapsule'
import { AuroraBackground, AuroraOrb } from '@/ui/aurora'
import { usePalette } from '@/ui/theme'

export default function StateGallery() {
  const { settings } = useStore(settingsStore)
  const reduceMotion = useReduceMotion()
  const p = usePalette(settings)
  const { only } = useLocalSearchParams<{ only?: string }>()
  const all = useMemo(() => presenceFixtures(), [])
  const fixtures = useMemo(() => {
    const keys = (only || '').split(',').map((k) => k.trim()).filter(Boolean)
    return keys.length ? all.filter((f) => keys.some((k) => f.label.includes(k))) : all
  }, [all, only])
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <AuroraBackground p={p} />
      <ScrollView contentContainerStyle={{ padding: 12, gap: 18 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>
          样本 {fixtures.length}{fixtures.length === all.length ? '' : `/${all.length}`} 条{only ? `（only=${only}）` : ''} · 主题跟随设置页 · 按钮在这里只记录不上行
        </Text>
        {fixtures.map((f) => (
          <View key={f.label} testID={`state-${f.label}`} style={{ gap: 6, borderTopWidth: 1, borderColor: p.line, paddingTop: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
              <Text style={{ color: p.fg3, fontSize: p.font(11), flex: 1 }}>{f.label} · primary={f.snapshot.primary}</Text>
              <Chip p={p} tone={f.producible ? 'accent' : 'amber'} text={f.producible ? '真栈可产' : '仅样本'} />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <AuroraOrb size={44} state={f.snapshot.primary} dim={f.snapshot.dim} animated={!reduceMotion} />
              <View style={{ flex: 1 }}>
                <PresenceCapsule p={p} fontScale={settings.fontScale} snapshot={f.snapshot} />
              </View>
            </View>
            <FocusDock
              p={p}
              fontScale={settings.fontScale}
              snapshot={f.snapshot}
              onConfirm={() => {}}
              onCancelTurn={() => {}}
              onReenableBargeIn={() => {}}
            />
            <Text style={{ color: p.fg3, fontSize: p.font(10) }}>
              transport={f.snapshot.transport} capture={f.snapshot.capture} agent={f.snapshot.agent} mic={f.snapshot.privacy.mic} camera={f.snapshot.privacy.camera}
            </Text>
          </View>
        ))}
      </ScrollView>
    </View>
  )
}
