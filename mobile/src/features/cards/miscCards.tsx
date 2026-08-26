// 澄清 / 提醒 / 场景族卡片：intent_choice / reminder_list / reminder_card（M1-4 首批）
// + scene_card / scene_list / vision_answer（M3-1）。
// 澄清卡按钮回发 send_text 经普通 send——candidates 拦截层自动补 clarify_resume=1
// （与语音「第N个」同一条路，sendRouter.ts）。
import { Pressable, Text, View } from 'react-native'

import type {
  IntentChoiceCard,
  ReminderCard,
  ReminderItem,
  ReminderListCard,
  SceneCard,
  SceneItem,
  SceneListCard,
  VisionAnswerCard,
} from '@shared/types.ts'

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

// ─────────────────────────── M3-1 增量 ───────────────────────────

const SCENE_CONTEXT: Record<SceneCard['context'], { label: string; tone: 'accent' | 'amber' }> = {
  confirm: { label: '待确认', tone: 'amber' },
  created: { label: '已保存', tone: 'accent' },
  activated: { label: '已开启', tone: 'accent' },
  suggest: { label: 'AI 建议', tone: 'amber' },
}

/** 场景卡：一张卡复用四态（confirm/created/activated/suggest）。
 *  `danger` 步骤打「需确认」角标——卡上先让用户看见，真正的二次确认仍由
 *  全局确认条走 `is_confirmation=true`（VAL 安全门控，卡片按钮不是授权）。 */
export function SceneSingle({ p, card, onSend }: { p: Palette; card: SceneCard; onSend: SendFn }) {
  const meta = SCENE_CONTEXT[card.context] || SCENE_CONTEXT.created
  const steps = card.actions_preview || []
  return (
    <CardShell p={p} title={card.name} right={<Chip p={p} tone={meta.tone} text={meta.label} />}>
      {card.description ? (
        <Text style={{ color: p.fg3, fontSize: p.font(12), lineHeight: p.font(19) }}>{card.description}</Text>
      ) : null}
      <View style={{ gap: 6 }}>
        {steps.map((s, i) => (
          <View key={i} style={{ flexDirection: 'row', alignItems: 'center', gap: 9 }}>
            <View
              style={{
                width: 18,
                height: 18,
                borderRadius: 6,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: p.line,
              }}
            >
              <Text style={{ color: p.fg3, fontSize: p.font(10), fontWeight: '700' }}>{i + 1}</Text>
            </View>
            <Text style={{ color: p.fg1, fontSize: p.font(13), flex: 1 }} numberOfLines={2}>
              {s.label}
            </Text>
            {s.danger ? <Chip p={p} tone="amber" text="需确认" /> : null}
          </View>
        ))}
      </View>
      <CardButtons p={p} onSend={onSend} buttons={card.buttons} />
    </CardShell>
  )
}

/** 场景列表卡：区分「我建的」与「内置」；条目可点 → 回发「开启X」 */
export function SceneList({ p, card, onSend }: { p: Palette; card: SceneListCard; onSend: SendFn }) {
  const group = (title: string, items: SceneItem[]) =>
    items.length ? (
      <View style={{ gap: 6 }}>
        <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
          {title} · {items.length}
        </Text>
        {items.map((s) => (
          <Pressable
            key={s.id}
            onPress={() => onSend(`开启${s.name}`)}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 10,
              paddingHorizontal: 12,
              paddingVertical: 9,
              borderRadius: 12,
              borderWidth: 1,
              borderColor: p.line,
              backgroundColor: p.panel,
            }}
          >
            <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }}>{s.name}</Text>
            {s.description ? (
              <Text style={{ color: p.fg3, fontSize: p.font(11), flex: 1 }} numberOfLines={1}>
                {s.description}
              </Text>
            ) : (
              <View style={{ flex: 1 }} />
            )}
            {s.action_count ? (
              <Text style={{ color: p.fg3, fontSize: p.font(11) }}>{s.action_count} 步</Text>
            ) : null}
          </Pressable>
        ))}
      </View>
    ) : null

  return (
    <CardShell p={p} title="场景">
      {group('我建的', card.mine || [])}
      {group('内置', card.builtin || [])}
      <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
        说「创建钓鱼模式：座椅放平、氛围灯调暗」就能造一个
      </Text>
    </CardShell>
  )
}

/** 看一看卡（M4 P4）：单帧图片问答的结果。
 *  `simulated` 角标**必须渲染**——PoC 没有车外摄像头，画面来自设备摄像头，
 *  卡片得如实说（同 sim.adas / 演示商户的诚实标注惯例）。 */
export function VisionAnswer({ p, card }: { p: Palette; card: VisionAnswerCard; onSend: SendFn }) {
  return (
    <CardShell
      p={p}
      title={card.question || '看一看'}
      right={card.simulated ? <Chip p={p} text="模拟车外摄像头" /> : undefined}
    >
      <Text style={{ color: p.fg1, fontSize: p.font(14), lineHeight: p.font(22) }}>{card.answer}</Text>
    </CardShell>
  )
}
