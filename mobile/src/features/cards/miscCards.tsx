// 澄清 / 提醒族卡片（M1-4 首批）：intent_choice / reminder_list / reminder_card。
// 澄清卡按钮回发 send_text 经普通 send——candidates 拦截层自动补 clarify_resume=1
// （与语音「第N个」同一条路，sendRouter.ts）。
import { Pressable, Text, View } from 'react-native'

import type { IntentChoiceCard, ReminderCard, ReminderItem, ReminderListCard } from '@shared/types.ts'

import type { Palette } from '../../ui/theme'
import { CardButtons, CardShell, Chip, type SendFn } from './parts'

export function IntentChoice({ p, card, onSend }: { p: Palette; card: IntentChoiceCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title="你是想…">
      <Text style={{ color: p.fg1, fontSize: p.font(14) }}>{card.question}</Text>
      <View style={{ gap: 8 }}>
        {(card.options || []).map((o, i) => (
          <Pressable
            key={i}
            onPress={() => onSend(o.send_text)}
            style={{
              backgroundColor: p.accentSoft,
              borderRadius: 10,
              paddingHorizontal: 12,
              paddingVertical: 10,
            }}
          >
            <Text style={{ color: p.accent, fontSize: p.font(14) }}>
              {i + 1}. {o.label}
            </Text>
          </Pressable>
        ))}
      </View>
    </CardShell>
  )
}

const STATUS_LABEL: Record<ReminderItem['status'], string> = {
  pending: '待提醒',
  fired: '已提醒',
  done: '已完成',
  cancelled: '已取消',
}

function ReminderRow({ p, item }: { p: Palette; item: ReminderItem }) {
  const dim = item.status === 'done' || item.status === 'cancelled'
  return (
    <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 2 }}>
      <Text style={{ fontSize: p.font(13) }}>{item.kind === 'todo' ? '☐' : '⏰'}</Text>
      <View style={{ flex: 1 }}>
        <Text
          style={{
            color: dim ? p.fg3 : p.fg1,
            fontSize: p.font(14),
            textDecorationLine: item.status === 'done' ? 'line-through' : 'none',
          }}
          numberOfLines={1}
        >
          {item.title}
        </Text>
        {item.time_display ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            {item.time_display}
            {item.recur_label ? ` · ${item.recur_label}` : ''}
          </Text>
        ) : null}
      </View>
      <Chip p={p} text={STATUS_LABEL[item.status] || item.status} />
    </View>
  )
}

export function ReminderList({ p, card }: { p: Palette; card: ReminderListCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={`提醒 · ${card.date_label || (card.view === 'multi' ? '近期' : '今天')}`}>
      {(card.items || []).map((it) => (
        <ReminderRow key={it.id} p={p} item={it} />
      ))}
      {card.todos?.length ? (
        <>
          <Text style={{ color: p.fg3, fontSize: p.font(11), marginTop: 4 }}>待办</Text>
          {card.todos.map((it) => (
            <ReminderRow key={it.id} p={p} item={it} />
          ))}
        </>
      ) : null}
      {!(card.items || []).length && !(card.todos || []).length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>暂无提醒</Text>
      ) : null}
    </CardShell>
  )
}

const CONTEXT_TITLE: Record<ReminderCard['context'], string> = {
  created: '提醒已创建',
  updated: '提醒已改期',
  fired: '提醒到点',
  offer: '要不要设个提醒？',
}

export function ReminderSingle({ p, card, onSend }: { p: Palette; card: ReminderCard; onSend: SendFn }) {
  return (
    <CardShell p={p} title={CONTEXT_TITLE[card.context] || '提醒'}>
      <ReminderRow p={p} item={card.item} />
      <CardButtons p={p} onSend={onSend} buttons={card.actions} />
    </CardShell>
  )
}
