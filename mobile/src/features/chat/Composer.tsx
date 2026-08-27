// 输入区（M1-3 + M2-2 PTT，Aurora Glass 复刻轮重皮）：快捷指令 chips + 文本输入 + 发送 + 按住说话。
// 视觉照 hmi shell.css .au-composer：语音按钮=小舟光球本体（52dp 圆形热区，车规触控），
// 录音→speaking（外扩波纹=「我在收你的声音」）、识别中→thinking、空闲→idle 呼吸；
// 发送按钮=极光渐变填充 + 辉光（§5 允许的主操作虹彩）。在飞轮存在时给「打断」（cancel 帧，U2 语义）。
import { useState } from 'react'
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native'

import { AuroraOrb, type OrbState } from '../../ui/aurora'
import { AURORA, type Palette } from '../../ui/theme'
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
  // 语音光球三态（hmi Composer 同款隐喻）：录音=speaking 波纹、识别=thinking 律动、空闲=idle 呼吸
  const orbState: OrbState = recording ? 'speaking' : finalizing ? 'thinking' : 'idle'
  // 一行状态条：识别中的实时文字优先，其次失败原因（两者都没有就不占高度）
  const finalizingHint = ptt?.slow ? '网络似乎不太顺，正在重试…' : '识别中…'
  const hint = ptt?.partial || (finalizing ? finalizingHint : '') || ptt?.error || ''
  const hintIsError = !ptt?.partial && !finalizing && !!ptt?.error

  return (
    <View
      style={{
        borderTopWidth: 1,
        borderColor: p.line,
        backgroundColor: p.dark ? 'rgba(6,8,15,0.55)' : 'rgba(237,241,250,0.72)',
      }}
    >
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
              backgroundColor: p.fill,
              borderWidth: 1,
              borderColor: p.fill2,
              borderRadius: 999,
              paddingHorizontal: 14,
              paddingVertical: 7,
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
      <View style={{ flexDirection: 'row', gap: 10, padding: 10, alignItems: 'flex-end' }}>
        {ptt ? (
          <Pressable
            onPressIn={ptt.pressDown}
            onPressOut={ptt.pressUp}
            disabled={finalizing}
            accessibilityLabel="按住说话"
            style={{
              width: 52,
              height: 52,
              borderRadius: 26,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: recording ? p.accentSoft : 'transparent',
            }}
          >
            <AuroraOrb size={40} state={orbState} animated />
          </Pressable>
        ) : null}
        <TextInput
          testID="composer-input"
          style={{
            flex: 1,
            backgroundColor: p.fill,
            borderWidth: 1,
            borderColor: p.fill2,
            borderRadius: 14,
            paddingHorizontal: 14,
            paddingVertical: 10,
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
              borderWidth: 1,
              borderColor: 'rgba(245,158,11,0.3)',
              borderRadius: 14,
              paddingHorizontal: 14,
              minHeight: 44,
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: p.amber, fontSize: p.font(14) }}>■ 打断</Text>
          </Pressable>
        ) : null}
        <Pressable
          testID="composer-send"
          onPress={submit}
          style={{
            experimental_backgroundImage: AURORA.gradient,
            borderRadius: 14,
            paddingHorizontal: 18,
            minHeight: 44,
            justifyContent: 'center',
            boxShadow: '0 4px 22px rgba(91,140,255,0.45)',
          }}
        >
          <Text style={{ color: '#fff', fontSize: p.font(15), fontWeight: '600' }}>发送</Text>
        </Pressable>
      </View>
    </View>
  )
}
