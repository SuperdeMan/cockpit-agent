import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Pressable, ScrollView, Text, View, type ViewToken } from 'react-native'
import type { Msg } from '@shared/types.ts'
import type { SessionCore } from '@/core/session/store'
import type { InteractionScope } from '@/core/session/interactionScope'
import { CardRenderer } from '@/features/cards/CardRenderer'
import { useAssistant } from './AssistantProvider'

/** 不把 FlashList 的 render/测量当呈现；只消费当前活跃记录区的 viewability 事件。 */
export function useProactiveViewability(core: SessionCore, scope: InteractionScope, exposed: boolean) {
  const allowed = useRef(false)
  useLayoutEffect(() => { allowed.current = exposed; return () => { allowed.current = false } }, [exposed])
  return useCallback(({ viewableItems }: { viewableItems: ViewToken<Msg>[] }) => {
    if (!allowed.current || !scope.canPresent() || scope.snapshot().route !== '/') return
    for (const item of viewableItems) if (item.isViewable) core.presentProactive(item.item.id)
  }, [core, scope])
}

/** 一条真实提醒的前台出口。待办 Modal/隐私栏/窗口失焦时不渲染，也不提前回执。 */
export function ProactivePresenter() {
  const runtime = useAssistant()
  if (!runtime || !runtime.scope.canPresent() || runtime.privacyOpen || runtime.dockExpanded) return null
  const message = runtime.state.messages.find((m) => {
    const delivery = runtime.state.proactiveDeliveries[m.id]
    return delivery && delivery.handledAt === undefined
  })
  if (!message) return null
  return <PresentedMessage key={`${message.id}:${runtime.facts.route}`} message={message} />
}

function PresentedMessage({ message }: { message: Msg }) {
  const runtime = useAssistant()!
  const { core, scope, p } = runtime
  const contentRef = useRef<View>(null)
  const [layout, setLayout] = useState<{ width: number; height: number } | null>(null)
  const active = useRef(true)
  useLayoutEffect(() => { active.current = true; return () => { active.current = false } }, [])
  const route = runtime.facts.route
  const privacyOpen = runtime.privacyOpen
  const dockExpanded = runtime.dockExpanded
  const available = useCallback(() => active.current && scope.canPresent() && scope.snapshot().route === route && !privacyOpen && !dockExpanded,
    [scope, route, privacyOpen, dockExpanded])
  useEffect(() => {
    let live = true
    if (!layout || layout.width <= 0 || layout.height <= 0) return
    // 再等一个画面机会；排队期间切后台/路由/会话后不可销账。
    const frame = requestAnimationFrame(() => {
      if (!live || !available()) return
      contentRef.current?.measureInWindow((x, y, width, height) => {
        if (live && available() && width > 0 && height > 0 && x >= 0 && y >= 0 &&
          x + width <= runtime.layout.width + 1 && y + height <= runtime.layout.height + 1) core.presentProactive(message.id)
      })
    })
    return () => { live = false; cancelAnimationFrame(frame) }
  }, [core, message.id, layout, available, runtime.layout.width, runtime.layout.height])
  return <View testID="proactive-presenter" style={{ maxHeight: 190, backgroundColor: p.bg, borderTopWidth: 1, borderColor: p.line, padding: 10 }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
      <Text accessibilityRole="header" style={{ color: p.amber, fontSize: p.font(13), flex: 1 }}>提醒</Text>
      <Pressable testID="proactive-dismiss" accessibilityRole="button" accessibilityLabel="收起这条提醒"
        style={{ minHeight: 48, minWidth: 48, justifyContent: 'center' }} onPress={() => {
          if (!available()) return
          core.presentProactive(message.id)
          core.handleProactive(message.id)
        }}><Text style={{ color: p.accent, fontSize: p.font(13) }}>收起</Text></Pressable>
    </View>
    <View ref={contentRef} testID="proactive-content" style={{ flexShrink: 1 }} onLayout={(e) => setLayout(e.nativeEvent.layout)}>
    <ScrollView>
      {message.text ? <Text style={{ color: p.fg1, fontSize: p.font(15) }}>{message.text}</Text> : null}
      {message.uiCard ? <CardRenderer p={p} card={message.uiCard} onSend={runtime.onSend} /> : null}
    </ScrollView>
    </View>
  </View>
}
