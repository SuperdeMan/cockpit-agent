// 调试屏「轮次时间线」（AR08 / A08-1 的读出口，AR09 / A09-1 的第一块）。
//
// 只读：这一屏**不发起任何业务**——不采集、不播放、不切播放器、不执行探针指令。
// 它显示的全部内容都来自 `core/obs/turnTimeline`，那一层本身也只记录不判断。
// 因为只读，prod 常驻包里也留着（同 capture-status / presence-trail 的边界）。
//
// 字段边界（AR09 §3.1）：只出关联 id（request/trace/bubble/operation）与相对时刻，
// **不出** token、连接 URL、完整用户话术、PCM。detail 是打点方写的短标签，白名单式使用。
//
// ⚠ 这屏最重要的一件事是**把「排定起播」和「真的出声」分开显示**：
// 没有外部声学读数时，「说完 → 听见」那一行必须是 NOT_MEASURED，
// 而不是拿 play_scheduled 顶上去——那正是 AR08 要消灭的那句谎话。
import * as Clipboard from 'expo-clipboard'
import { useState, useSyncExternalStore } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { formatBuildLabel, readBuildInfo } from '@/core/buildInfo'
import {
  interactionTimelines,
  subscribeTimelines,
  timelineDroppedTurns,
  timelineMetrics,
  TIMELINE_CAP,
  type TurnTimeline,
} from '@/core/obs/turnTimeline'
import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'

/** 复制出去的 JSON 上限（AR09 §3.1）。超了就截，并且**把截断这件事写进内容里** */
const COPY_MAX_BYTES = 256 * 1024

function hms(ms: number): string {
  const d = new Date(ms)
  const two = (n: number): string => String(n).padStart(2, '0')
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`
}

/** null ⇒ NOT_MEASURED。**不许打印 0ms**——0 会被读成「非常快」 */
function ms(v: number | null): string {
  return v === null ? 'NOT_MEASURED' : `${Math.round(v)}ms`
}

function metricLines(t: TurnTimeline): string[] {
  const m = timelineMetrics(t)
  return [
    `说完→听见(声学)  ${ms(m.utteranceEndToAudible)}   [来源 ${m.firstAudioSource}]`,
    `说完→排定(代理)  ${ms(m.utteranceEndToScheduled)}`,
    `端点→ASR定稿     ${ms(m.endpointToAsrFinal)}`,
    `定稿→发送        ${ms(m.asrFinalToRequestSent)}`,
    `发送→首段有效文本 ${ms(m.requestSentToFirstText)}`,
    `有效文本→送合成   ${ms(m.firstTextToTtsSent)}`,
    `送合成→首片PCM   ${ms(m.ttsSentToFirstPcm)}`,
    `首片PCM→排定     ${ms(m.firstPcmToScheduled)}`,
    `发送→排定(文本轮) ${ms(m.requestSentToScheduled)}`,
    `终态             ${m.terminal}`,
  ]
}

export default function TurnTimelineScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  // 订阅与读快照同一份（useSyncExternalStore）；这一层不做任何本地缓存
  const turns = useSyncExternalStore(subscribeTimelines, interactionTimelines)
  const dropped = timelineDroppedTurns()
  const [copied, setCopied] = useState('')

  const payload = {
    schema_version: 1,
    build: formatBuildLabel(readBuildInfo()),
    captured_at: new Date().toISOString(),
    dropped_turns: dropped,
    turns: turns.map((t) => ({
      interactionId: t.interactionId,
      kind: t.kind,
      startedAtWall: t.startedAtWall,
      ids: t.ids,
      dropped: t.dropped,
      firstAudioSource: t.firstAudioSource,
      metrics: timelineMetrics(t),
      marks: t.marks.map((k) => ({
        event: k.event,
        offsetMs: Math.round(k.at - t.startedAtMono),
        domain: k.domain,
        ...(k.detail ? { detail: k.detail } : {}),
      })),
    })),
  }

  const copy = (): void => {
    let text = JSON.stringify(payload, null, 2)
    let note = ''
    if (text.length > COPY_MAX_BYTES) {
      text = text.slice(0, COPY_MAX_BYTES)
      note = `（已截断到 ${COPY_MAX_BYTES} 字节）`
      text += '\n/* TRUNCATED */'
    }
    void Clipboard.setStringAsync(text)
    setCopied('已复制 ' + text.length + ' 字节' + note)
  }

  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <View style={{ padding: 12, gap: 6 }}>
        <Text style={{ color: p.fg2, fontSize: p.font(12) }}>
          轮次时间线 {turns.length}/{TIMELINE_CAP}（最新在后；只读、不上传、不发起业务）
        </Text>
        <Text testID="timeline-dropped" style={{ color: p.fg3, fontSize: p.font(11) }}>
          超容量丢弃 {dropped} 轮 · 构建 {formatBuildLabel(readBuildInfo())}
        </Text>
        <Pressable
          accessibilityRole="button"
          testID="timeline-copy"
          onPress={copy}
          style={{
            minHeight: 44,
            justifyContent: 'center',
            paddingHorizontal: 12,
            borderRadius: 12,
            borderWidth: 1,
            borderColor: p.fill2,
            backgroundColor: p.fill,
            alignSelf: 'flex-start',
          }}
        >
          <Text style={{ color: p.fg1, fontSize: p.font(13) }}>复制 JSON（手动导出）</Text>
        </Pressable>
        {copied ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{copied}</Text> : null}
      </View>

      <ScrollView contentContainerStyle={{ padding: 12, gap: 14 }}>
        {turns.length === 0 ? (
          <Text style={{ color: p.fg3, fontSize: p.font(12) }}>
            还没有轮次。发一句话或按住光球说一句，这里就会出现一条。
          </Text>
        ) : null}
        {[...turns].reverse().map((t) => (
          <View
            key={t.interactionId}
            testID="timeline-turn"
            style={{ gap: 4, borderTopWidth: 1, borderTopColor: p.fill2, paddingTop: 8 }}
          >
            <Text style={{ color: p.fg1, fontSize: p.font(12) }}>
              {hms(t.startedAtWall)} · {t.kind} · {t.interactionId}
              {t.dropped ? ` · 丢弃事件 ${t.dropped}` : ''}
            </Text>
            <Text selectable style={{ color: p.fg3, fontSize: p.font(10), fontFamily: 'monospace' }}>
              req={t.ids.requestId ?? '—'} trace={t.ids.traceId ?? '—'} bubble={t.ids.bubbleId ?? '—'}
              {t.ids.operationId ? ` op=${t.ids.operationId}` : ''}
            </Text>
            {metricLines(t).map((l, i) => (
              <Text
                key={i}
                testID="timeline-metric"
                selectable
                style={{ color: p.fg2, fontSize: p.font(10), fontFamily: 'monospace' }}
              >
                {l}
              </Text>
            ))}
            <Text selectable style={{ color: p.fg3, fontSize: p.font(10), fontFamily: 'monospace' }}>
              {t.marks
                .map((k) => `+${Math.round(k.at - t.startedAtMono)} ${k.event}${k.detail ? '(' + k.detail + ')' : ''}`)
                .join('  ')}
            </Text>
          </View>
        ))}
      </ScrollView>
    </View>
  )
}
