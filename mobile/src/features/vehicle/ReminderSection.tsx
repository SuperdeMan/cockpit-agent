// 平板右面板的「今日提醒」段（M3-2）。
// 分组推导复用 `@shared/reminderStage.mjs::groupByDay`——**「今天/明天/后天/几月几日」
// 这套标签在两端必须是同一份**：右面板说「明天」而聊天流里的卡说「8月28日」，
// 用户没法判断这是两条提醒还是同一条。
//
// 数据来源刻意是**最近一张 reminder_list 卡**而不是另起一路查询：App 与后端之间
// 只有主链一条通道，右面板自己去拉一遍等于凭空多一个数据源（也多一份会漂的真相）。
import { Text, View } from 'react-native'

import { groupByDay } from '@shared/reminderStage.mjs'
import type { Msg, ReminderItem, ReminderListCard, UiCard } from '@shared/types.ts'

import type { Palette } from '../../ui/theme'

/* eslint-disable @typescript-eslint/no-explicit-any */

/** 消息流里最近一张 reminder_list 卡（含 card_group 内嵌的那张） */
export function latestReminderCard(messages: Msg[]): ReminderListCard | null {
  const dig = (card: UiCard | undefined): ReminderListCard | null => {
    if (!card || typeof card !== 'object') return null
    if ((card as any).type === 'reminder_list') return card as ReminderListCard
    if ((card as any).type === 'card_group') {
      const items: UiCard[] = (card as any).items || []
      // 组内从后往前找：后面的更接近「这一轮真正在说的那张」
      for (let i = items.length - 1; i >= 0; i -= 1) {
        const hit = dig(items[i])
        if (hit) return hit
      }
    }
    return null
  }
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const hit = dig(messages[i].uiCard)
    if (hit) return hit
  }
  return null
}

const STATUS_DIM = new Set(['done', 'cancelled'])

function Row({ p, item }: { p: Palette; item: ReminderItem }) {
  const dim = STATUS_DIM.has(item.status)
  return (
    <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', paddingVertical: 2 }}>
      <Text style={{ fontSize: p.font(12) }}>{item.kind === 'todo' ? '☐' : '⏰'}</Text>
      <Text
        style={{
          color: dim ? p.fg3 : p.fg1,
          fontSize: p.font(12),
          flex: 1,
          textDecorationLine: item.status === 'done' ? 'line-through' : 'none',
        }}
        numberOfLines={1}
      >
        {item.title}
      </Text>
      {item.time_display ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{item.time_display}</Text>
      ) : null}
    </View>
  )
}

export function ReminderSection({ p, messages }: { p: Palette; messages: Msg[] }) {
  const card = latestReminderCard(messages)
  const items: ReminderItem[] = [...(card?.items || []), ...(card?.todos || [])]
  // 只算未完成的：右面板是「还要做什么」，不是历史台账
  const live = items.filter((it) => !STATUS_DIM.has(it.status))
  const { groups, more } = groupByDay(live, Date.now(), 6)

  return (
    <View style={{ gap: 6 }}>
      <Text style={{ color: p.fg3, fontSize: p.font(12) }}>提醒</Text>
      {!card ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>问一句「我今天有什么提醒」就会出现在这里</Text>
      ) : !live.length ? (
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>没有待办提醒</Text>
      ) : (
        <>
          {groups.map((g: { label: string; items: ReminderItem[] }) => (
            <View key={g.label} style={{ gap: 2 }}>
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{g.label}</Text>
              {g.items.map((it) => (
                <Row key={it.id} p={p} item={it} />
              ))}
            </View>
          ))}
          {/* 无 fire_at 的条目 groupByDay 会滤掉——数不上就明说，别让它们静默消失 */}
          {live.some((it) => !it.fire_at_ms) ? (
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
              另有 {live.filter((it) => !it.fire_at_ms).length} 条未定时
            </Text>
          ) : null}
          {more ? <Text style={{ color: p.fg3, fontSize: p.font(11) }}>还有 {more} 条</Text> : null}
        </>
      )}
    </View>
  )
}
