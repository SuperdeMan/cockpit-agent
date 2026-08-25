// 输入区（实施计划 M1-3）：文本输入 + 发送 + 快捷指令 chips（settings.quickCommands）。
// 在飞轮存在时给「打断」（cancel 帧，U2 语义）。PTT 语音按钮随 M2-2 加。
import { useState } from 'react'
import { Pressable, ScrollView, Text, TextInput, View } from 'react-native'

import type { Palette } from '../../ui/theme'

export function Composer({
  p,
  quickCommands,
  busy,
  onSend,
  onInterrupt,
}: {
  p: Palette
  quickCommands: string[]
  /** 有在飞轮（pending/streaming/process 任一）→ 显示打断 */
  busy: boolean
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
      <View style={{ flexDirection: 'row', gap: 8, padding: 10, alignItems: 'flex-end' }}>
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
          placeholder="和小舟说点什么…"
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
