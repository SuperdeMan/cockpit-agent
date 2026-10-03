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

import { CardIcon } from '../cards/parts'
import type { Palette } from '../../ui/theme'
import { textStyle } from '../../ui/tokens'

 

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

/** 组内一行（Figma 07 A-1 提醒段）：时刻在前（accent numeric/m，组标签已经说了哪天，这里只给本地 HH:mm）+ 标题 body/m。
 *  没有触发时刻的（待办）给一个方框图标占时刻位 */
function Row({ p, item }: { p: Palette; item: ReminderItem }) {
  const dim = STATUS_DIM.has(item.status)
  const at = item.fire_at_ms ? new Date(item.fire_at_ms) : null
  const clock = at ? `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}` : ''
  return (
    <View style={{ flexDirection: 'row', gap: 12, alignItems: 'center', paddingVertical: 6 }}>
      {clock ? (
        <Text style={[textStyle('numericM', p.fontScale), { color: dim ? p.fg3 : p.accent, minWidth: 44 }]}>{clock}</Text>
      ) : (
        <View style={{ minWidth: 44 }}>
          <CardIcon p={p} name={item.kind === 'todo' ? 'square' : 'clock'} size={16} color={dim ? p.fg3 : p.fg2} />
        </View>
      )}
      <Text
        style={[
          textStyle('bodyM', p.fontScale),
          { color: dim ? p.fg3 : p.fg1, flex: 1, textDecorationLine: item.status === 'done' ? 'line-through' : 'none' },
        ]}
        numberOfLines={1}
      >
        {item.title}
      </Text>
    </View>
  )
}

export function ReminderSection({ p, messages }: { p: Palette; messages: Msg[] }) {
  const card = latestReminderCard(messages)
  const items: ReminderItem[] = [...(card?.items || []), ...(card?.todos || [])]
  // 只算未完成的：右面板是「还要做什么」，不是历史台账
  const live = items.filter((it) => !STATUS_DIM.has(it.status))
  // 「今天/明天」的分组要按**这一帧**的墙钟算：跨过午夜后下一次重渲就该换组。
  // 存进 state 或 useMemo 都会把日界冻在挂载那一刻。
  // eslint-disable-next-line react-hooks/purity -- 见上
  const { groups, more } = groupByDay(live, Date.now(), 6)

  return (
    <View style={{ gap: 8 }}>
      <Text style={[textStyle('labelM', p.fontScale), { color: p.fg3 }]}>提醒</Text>
      {!card ? (
        <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>问一句「我今天有什么提醒」就会出现在这里</Text>
      ) : !live.length ? (
        <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>没有待办提醒</Text>
      ) : (
        <>
          {groups.map((g: { label: string; items: ReminderItem[] }) => (
            <View key={g.label} style={{ gap: 0 }}>
              <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>{g.label}</Text>
              {g.items.map((it) => (
                <Row key={it.id} p={p} item={it} />
              ))}
            </View>
          ))}
          {/* 无 fire_at 的条目 groupByDay 会滤掉——数不上就明说，别让它们静默消失 */}
          {live.some((it) => !it.fire_at_ms) ? (
            <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>
              另有 {live.filter((it) => !it.fire_at_ms).length} 条未定时
            </Text>
          ) : null}
          {more ? <Text style={[textStyle('caption', p.fontScale), { color: p.fg3 }]}>还有 {more} 条</Text> : null}
        </>
      )}
    </View>
  )
}
