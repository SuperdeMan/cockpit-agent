// mobile/src/app/presence-trail.tsx
// 调试屏「在场轨迹」（方案 §11.5，v2.2 🔁-1）：PresenceSnapshot 变化轨迹（20 条环形）+ 采集激活日志
// （activityLog.list() 的第一个消费方，评审 D8）。dev 取证入口，不进主链路；不上传。
import * as Clipboard from 'expo-clipboard'
import { useEffect, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { activityLog } from '@/core/presence/activityLog'
import { presenceTrail, type TrailEntry } from '@/core/presence/presenceTrail'
import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'

function hms(ms: number): string {
  const d = new Date(ms)
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, '0')}`
}

function line(e: TrailEntry): string {
  if (e.kind === 'mark') return `${hms(e.at)} ◇ ${e.label}`
  return `${hms(e.at)} ${e.primary} · ${e.input}${e.capsule ? ` · 「${e.capsule}」` : ''}\n   轴 ${e.changedAxes.join(',')}\n   输入 ${e.changedInputs.join(',') || '—'}`
}

export default function PresenceTrailScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const [, force] = useState(0)
  useEffect(() => presenceTrail.subscribe(() => force((n) => n + 1)), [])
  useEffect(() => activityLog.subscribe(() => force((n) => n + 1)), [])
  const trail = presenceTrail.list()
  const acts = activityLog.list()
  const btn = (label: string, run: () => void) => (
    <Pressable
      accessibilityRole="button"
      onPress={run}
      style={{
        minHeight: 44,
        justifyContent: 'center',
        paddingHorizontal: 12,
        borderRadius: 12,
        borderWidth: 1,
        borderColor: p.fill2,
        backgroundColor: p.fill,
      }}
    >
      <Text style={{ color: p.fg1, fontSize: p.font(13) }}>{label}</Text>
    </Pressable>
  )
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <View style={{ flexDirection: 'row', gap: 8, padding: 12 }}>
        {btn('复制 JSON', () => void Clipboard.setStringAsync(JSON.stringify({ trail, activity: acts })))}
        {btn('清空轨迹', () => presenceTrail.clear())}
      </View>
      <ScrollView contentContainerStyle={{ padding: 12, gap: 10 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>在场轨迹 {trail.length}/20（最新在前；只记轴变化，不上传）</Text>
        {trail.map((e, i) => (
          <Text key={i} testID="trail-entry" style={{ color: p.fg2, fontSize: p.font(11), fontFamily: 'monospace' }}>
            {line(e)}
          </Text>
        ))}
        <Text style={{ color: p.fg2, fontSize: p.font(12), marginTop: 12 }}>采集激活 {acts.length}/20（麦 / 摄像头 / 定位为什么开了）</Text>
        {acts.map((a, i) => (
          <Text key={i} style={{ color: p.fg2, fontSize: p.font(11), fontFamily: 'monospace' }}>
            {hms(a.at)} {a.source} · {a.note}
          </Text>
        ))}
      </ScrollView>
    </View>
  )
}
