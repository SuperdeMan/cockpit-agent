// Read-only acquisition diagnostics. Route parameters never initiate collection or change settings.
import { useEffect, useState, useSyncExternalStore } from 'react'
import { Pressable, ScrollView, Text } from 'react-native'
import { useStore } from 'zustand'
import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'
import { getAudioCaptureCounters, getAudioCaptureSnapshot, subscribeAudioCapture } from '@/core/voice/captureFacts'
import { getAudioPlaybackCounters, getAudioPlaybackSnapshot, subscribeAudioPlayback } from '@/core/voice/playbackFacts'
import { getVisionCaptureSnapshot, subscribeVisionCapture } from '@/core/vision/frame'
import { memoryCamera } from '@/core/vision/nativeCamera'
import { readBuildInfo } from '@/core/buildInfo'

export default function CaptureStatus() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const textStyle = { color: p.fg1, fontSize: p.font(14) }
  const audio = useSyncExternalStore(subscribeAudioCapture, getAudioCaptureSnapshot)
  const playback = useSyncExternalStore(subscribeAudioPlayback, getAudioPlaybackSnapshot)
  const vision = useSyncExternalStore(subscribeVisionCapture, getVisionCaptureSnapshot)
  const [native, setNative] = useState<Record<string, number | boolean> | null>(null)
  const refresh = async () => { setNative(await memoryCamera()?.getMemoryCaptureStatsAsync().catch(() => null) ?? null) }
  useEffect(() => { void refresh() }, [])
  return <ScrollView style={{ backgroundColor: p.bg }} contentContainerStyle={{ padding: 20, gap: 16 }}>
    <Text style={textStyle}>采集状态（只读）</Text>
    <Text style={textStyle}>此页只读取状态与计数，不录音、不拍摄、不上传。</Text>
    <Text style={textStyle} selectable testID="capture-build">{JSON.stringify(readBuildInfo())}</Text>
    <Text style={textStyle} selectable testID="capture-audio">{JSON.stringify(audio)}</Text>
    <Text style={textStyle} selectable testID="capture-audio-counters">{JSON.stringify(getAudioCaptureCounters())}</Text>
    {/* AR03：播放事实与起停计数（只读）。停播的真机取证读它——「队列清空了没有」不看屏上的键 */}
    <Text style={textStyle} selectable testID="capture-playback">{JSON.stringify({ ...playback, ...getAudioPlaybackCounters() })}</Text>
    <Text style={textStyle} selectable testID="capture-vision">{JSON.stringify(vision)}</Text>
    <Text style={textStyle} selectable testID="capture-native">{JSON.stringify(native)}</Text>
    <Pressable accessibilityRole="button" accessibilityLabel="刷新采集计数" onPress={() => { void refresh() }} style={{ minHeight: 48, justifyContent: 'center' }}>
      <Text style={{ ...textStyle, color: p.accent }}>刷新采集计数</Text>
    </Pressable>
  </ScrollView>
}
