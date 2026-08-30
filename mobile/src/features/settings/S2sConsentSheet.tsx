// mobile/src/features/settings/S2sConsentSheet.tsx
// 端到端挡位的一次性显式同意（方案 §5.2.2「设置里把挡位从三段式切到端到端时弹一次性显式同意（不是只有开关）」）。
// G0 实色（§5.11：隐私说明不许半透明）。文案逐条对应 CLAUDE.md §5「唯一的受控例外」三条件。
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'

import type { FontScalePref } from '@/core/settings/store'
import { RADIUS, TARGET, TYPE, scale } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

export const S2S_CONSENT_TEXT = [
  '端到端语音会把你说话的原始音频上传到服务器上的语音大模型，而三段式只上传识别后的文字。',
  '只在你唤醒之后的对话窗内采集；没唤醒时一帧都不传。',
  '它不能直接执行任何动作：涉及车控、支付、导航、账户或记忆的话会交回文本主链，经权限与二次确认。',
  '随时可以在这里切回三段式；切回后立即停止上传。',
]

export function S2sConsentSheet({
  p,
  fontScale,
  visible,
  onAccept,
  onDecline,
}: {
  p: Palette
  fontScale: FontScalePref
  visible: boolean
  onAccept(): void
  onDecline(): void
}) {
  const solid = p.dark ? '#0A0E1A' : '#FFFFFF'
  const h = scale(TARGET.parked, 'target', fontScale)
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onDecline}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={onDecline} accessibilityLabel="仍用三段式" />
      <View
        testID="s2s-consent"
        style={{
          backgroundColor: solid,
          borderTopLeftRadius: RADIUS['2xl'],
          borderTopRightRadius: RADIUS['2xl'],
          padding: 16,
          gap: 12,
        }}
      >
        <Text style={{ color: p.fg1, fontSize: scale(TYPE.h2, 'text', fontScale), fontWeight: '600' }}>切到端到端语音之前</Text>
        <ScrollView style={{ maxHeight: 260 }}>
          {S2S_CONSENT_TEXT.map((line, i) => (
            <Text
              key={i}
              style={{
                color: p.fg2,
                fontSize: scale(TYPE.body - 1, 'text', fontScale),
                lineHeight: scale(22, 'line', fontScale),
                paddingVertical: 3,
              }}
            >
              {i + 1}. {line}
            </Text>
          ))}
        </ScrollView>
        <View style={{ flexDirection: 'row', gap: 10 }}>
          <Pressable
            testID="s2s-consent-decline"
            accessibilityRole="button"
            onPress={onDecline}
            style={{
              flex: 1,
              minHeight: h,
              borderRadius: RADIUS.md,
              borderWidth: 1,
              borderColor: p.fill2,
              backgroundColor: p.fill,
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: p.fg2, fontSize: scale(TYPE.body, 'text', fontScale) }}>仍用三段式</Text>
          </Pressable>
          <Pressable
            testID="s2s-consent-accept"
            accessibilityRole="button"
            onPress={onAccept}
            style={{
              flex: 2,
              minHeight: h,
              borderRadius: RADIUS.md,
              borderWidth: 1,
              borderColor: 'rgba(245,158,11,0.38)',
              backgroundColor: p.amberSoft,
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale), fontWeight: '600' }}>
              我知道了，切到端到端
            </Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  )
}
