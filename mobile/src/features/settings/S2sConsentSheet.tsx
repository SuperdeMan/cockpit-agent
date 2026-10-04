// mobile/src/features/settings/S2sConsentSheet.tsx
// 端到端挡位的一次性显式同意（方案 §5.2.2「设置里把挡位从三段式切到端到端时弹一次性显式同意（不是只有开关）」）。
// G0 实色（§5.11：隐私说明不许半透明）。文案逐条对应 CLAUDE.md §5「唯一的受控例外」三条件。
// 版式照 Figma 04 页 S-6（v3 SheetPanel：surfaceHigh、顶角 28、把手 + 标题；编号 bodyM 三级字、正文一级字；
// 「仍用三段式」Tonal 占 1 份、「我知道了，切到端到端」Filled 占 2 份）。点遮罩 / 返回键 = 仍用三段式。
import { Modal, Pressable, ScrollView, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useLayoutEffect } from 'react'
import { useAssistant } from '@/features/assistant/AssistantProvider'

import type { FontScalePref } from '@/core/settings/store'
import { Button } from '@/ui/Button'
import { SheetPanel } from '@/ui/Sheet'
import type { Palette } from '@/ui/theme'
import { SPACE, textStyle } from '@/ui/tokens'

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
  const scope = useAssistant()?.scope
  useLayoutEffect(() => visible ? scope?.blockPresentation() : undefined, [scope, visible])
  const body = textStyle('bodyM', fontScale)
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onDecline}>
      <SafeAreaView edges={['top']} style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: p.scrim }}>
        <Pressable style={{ flex: 1 }} onPress={onDecline} accessibilityLabel="仍用三段式" />
        <View accessibilityViewIsModal style={{ maxHeight: '85%' }}>
          <SheetPanel
            p={p}
            fontScale={fontScale}
            testID="s2s-consent"
            title="切到端到端语音之前"
            style={{ maxHeight: '100%' }}
            actions={
              <View style={{ flexDirection: 'row', gap: SPACE[1] }}>
                <Button p={p} fontScale={fontScale} variant="tonal" label="仍用三段式" testID="s2s-consent-decline" onPress={onDecline} style={{ flex: 1 }} />
                <Button
                  p={p}
                  fontScale={fontScale}
                  variant="filled"
                  label="我知道了，切到端到端"
                  testID="s2s-consent-accept"
                  onPress={onAccept}
                  style={{ flex: 2 }}
                />
              </View>
            }
          >
            <ScrollView contentContainerStyle={{ gap: 10 }}>
              {S2S_CONSENT_TEXT.map((line, i) => (
                <View key={i} style={{ flexDirection: 'row', gap: SPACE[1] }}>
                  <Text style={[body, { color: p.fg3 }]}>{i + 1}.</Text>
                  <Text style={[body, { color: p.fg1, flex: 1 }]}>{line}</Text>
                </View>
              ))}
            </ScrollView>
          </SheetPanel>
        </View>
      </SafeAreaView>
    </Modal>
  )
}
