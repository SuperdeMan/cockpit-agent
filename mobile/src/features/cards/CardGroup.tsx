// card_group 渲染（方案 §5.2 规则 7 / §5.4）：主卡全展 + 其余「还有 N 张 ›」竖排展开
// （不做轮播——语音场景里轮播等于藏卡）。判据在 core/cards/cardGroup.ts；这里只渲染。
// 与 CardRenderer 互相 import 是**渲染期**的循环（子卡在函数体里才引用），与今天注册表的递归同形。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'

import { splitCardGroup } from '../../core/cards/cardGroup'
import type { Palette } from '../../ui/theme'
import { TARGET } from '../../ui/tokens'
import { CardRenderer } from './CardRenderer'
import type { SendFn } from './parts'

export function CardGroup({ p, items, onSend }: { p: Palette; items: unknown[]; onSend: SendFn }) {
  const [open, setOpen] = useState(false)
  const { main, rest } = splitCardGroup(items)
  if (!main) return null
  return (
    <View style={{ gap: 8 }}>
      <CardRenderer p={p} card={main} onSend={onSend} />
      {rest.length ? (
        <Pressable
          testID="card-group-more"
          accessibilityRole="button"
          onPress={() => setOpen((o) => !o)}
          style={{ minHeight: TARGET.parked, justifyContent: 'center' }}
        >
          <Text style={{ color: p.accent, fontSize: p.font(12) }}>{open ? '收起其余卡片 ⌃' : `还有 ${rest.length} 张 ›`}</Text>
        </Pressable>
      ) : null}
      {open ? rest.map((sub, i) => <CardRenderer key={i} p={p} card={sub} onSend={onSend} />) : null}
    </View>
  )
}
