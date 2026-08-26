// 输入区（实施计划 M1-3 + M2-2 PTT）：文本输入 + 发送 + 快捷指令 chips + 按住说话。
// 在飞轮存在时给「打断」（cancel 帧，U2 语义）。
import { useState } from 'react'
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native'

import type { Palette } from '../../ui/theme'
import type { PttHandle } from './usePtt'

export function Composer({
  p,
  quickCommands,
  busy,
  ptt,
  onSend,
  onInterrupt,
}: {
  p: Palette
  quickCommands: string[]
  /** 有在飞轮（pending/streaming/process 任一）→ 显示打断 */
  busy: boolean
  /** 语音输入把手；null=服务器未配置（没有 audioUrl 就没有语音） */
  ptt: PttHandle | null
  onSend(text: string): void
  onInterrupt(): void
}) {
  const [input, setInput] = useState('')
  const submit = () => {
    const text = input.trim()
    if (!text) return
    onSend(text)
    setInput('')
  }
  const recording = ptt?.state === 'recording'
  const finalizing = ptt?.state === 'finalizing'
  // 一行状态条：识别中的实时文字优先，其次失败原因（两者都没有就不占高度）
  const finalizingHint = ptt?.slow ? '网络似乎不太顺，正在重试…' : '识别中…'
  const hint = ptt?.partial || (finalizing ? finalizingHint : '') || ptt?.error || ''
  const hintIsError = !ptt?.partial && !finalizing && !!ptt?.error

  return (
    <View style={{ borderTopWidth: 1, borderColor: p.line, backgroundColor: p.bg }}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ gap: 8, paddingHorizontal: 12, paddingTop: 8 }}
      >
        {quickCommands.map((c) => (
          <Pressable
            key={c}
            onPress={() => onSend(c)}
            style={{
              borderWidth: 1,
              borderColor: p.line,
              borderRadius: 999,
              paddingHorizontal: 12,
              paddingVertical: 5,
            }}
          >
            <Text style={{ color: p.fg2, fontSize: p.font(12) }}>{c}</Text>
          </Pressable>
        ))}
      </ScrollView>
      {hint ? (
        <Text
          numberOfLines={2}
          style={{
            color: hintIsError ? p.amber : p.fg2,
            fontSize: p.font(13),
            paddingHorizontal: 14,
            paddingTop: 6,
          }}
        >
          {recording ? '🎙 ' : ''}
          {hint}
        </Text>
      ) : null}
      <View style={{ flexDirection: 'row', gap: 8, padding: 10, alignItems: 'flex-end' }}>
        {ptt ? (
          <Pressable
            onPressIn={ptt.pressDown}
            onPressOut={ptt.pressUp}
            disabled={finalizing}
            accessibilityLabel="按住说话"
            style={{
              backgroundColor: recording ? p.red : p.accentSoft,
              borderRadius: 12,
              paddingHorizontal: 13,
              minHeight: 42,
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: recording ? '#fff' : p.accent, fontSize: p.font(17) }}>
              {finalizing ? '…' : '🎙'}
            </Text>
          </Pressable>
        ) : null}
        <TextInput
          style={{
            flex: 1,
            borderWidth: 1,
            borderColor: p.line,
            borderRadius: 12,
            paddingHorizontal: 12,
            paddingVertical: 9,
            fontSize: p.font(15),
            color: p.fg1,
            maxHeight: 120,
          }}
          value={input}
          onChangeText={setInput}
          placeholder={recording ? '正在听…' : '和小舟说点什么…'}
          placeholderTextColor={p.fg3}
          multiline
          onSubmitEditing={submit}
          submitBehavior="blurAndSubmit"
          returnKeyType="send"
        />
        {busy ? (
          <Pressable
            onPress={onInterrupt}
            style={{
              backgroundColor: p.amberSoft,
              borderRadius: 12,
              paddingHorizontal: 14,
              minHeight: 42,
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: p.amber, fontSize: p.font(14) }}>■ 打断</Text>
          </Pressable>
        ) : null}
        <Pressable
          onPress={submit}
          style={{
            backgroundColor: p.accent,
            borderRadius: 12,
            paddingHorizontal: 16,
            minHeight: 42,
            justifyContent: 'center',
          }}
        >
          <Text style={{ color: '#fff', fontSize: p.font(15) }}>发送</Text>
        </Pressable>
      </View>
    </View>
  )
}
