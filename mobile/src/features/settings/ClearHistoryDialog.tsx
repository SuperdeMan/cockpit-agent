// mobile/src/features/settings/ClearHistoryDialog.tsx
// 清除对话记录的二次确认（Figma 04 页 S-5：v3 Dialog）。原来是系统 Alert——ColorOS 自己画的对话框，
// 不跟 App 的深浅、字阶与配色走。语义不变：只有点「清除」才清；「取消」、点遮罩、返回键都是取消。
import { Modal, Pressable, View } from 'react-native'

import type { FontScalePref } from '@/core/settings/store'
import { Button } from '@/ui/Button'
import { DialogPanel } from '@/ui/Sheet'
import type { Palette } from '@/ui/theme'
import { SPACE } from '@/ui/tokens'

export const CLEAR_HISTORY_TITLE = '清除对话记录'
export const CLEAR_HISTORY_MESSAGE = '会同时清空当前会话与本机保存的记录，不可恢复。'

export function ClearHistoryDialog({
  p,
  fontScale,
  visible,
  onConfirm,
  onCancel,
}: {
  p: Palette
  fontScale: FontScalePref
  visible: boolean
  onConfirm(): void
  onCancel(): void
}) {
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel} statusBarTranslucent>
      <View style={{ flex: 1, justifyContent: 'center', paddingHorizontal: SPACE[4] }}>
        <Pressable
          testID="settings-clear-history-scrim"
          accessibilityLabel="取消"
          onPress={onCancel}
          style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: p.scrim }}
        />
        <DialogPanel
          p={p}
          fontScale={fontScale}
          testID="settings-clear-history-dialog"
          title={CLEAR_HISTORY_TITLE}
          message={CLEAR_HISTORY_MESSAGE}
          actions={
            <>
              <Button p={p} fontScale={fontScale} variant="text" label="取消" testID="settings-clear-history-cancel" onPress={onCancel} />
              <Button p={p} fontScale={fontScale} variant="destructive" label="清除" testID="settings-clear-history-confirm" onPress={onConfirm} />
            </>
          }
        />
      </View>
    </Modal>
  )
}
