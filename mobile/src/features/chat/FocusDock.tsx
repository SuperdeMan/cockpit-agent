// mobile/src/features/chat/FocusDock.tsx
// 承诺面（方案 §5.3）：读 `commitment[]`（钉一项 + 其余个数）与 `degradation[]`（有出口的降级）。
// 材质 **G0 实色**（§5.11：确认/错误/隐私说明不许半透明；坑账 §9.36 同判据）。
// v3（Figma FocusDock/Item）：底 = surfaceHigh，确认类琥珀描边、错误 red 描边、其余 line；按钮是胶囊形的目标高；
// 确认键改琥珀实色 + onAmber 字（原来 amberSoft 底上的琥珀字），取消键中性 surfaceHighest。
// 确认按钮比例照 A-6.4：取消 flex1 / 确认 flex2；剩余时间**只读共享 TTL**（commitment.ts）。
import { useState } from 'react'
import { Linking, Modal, Pressable, ScrollView, Text, useWindowDimensions, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'

import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { confirmRemainingMs, pinCommitment, type DockItem } from '@/core/presence/commitment'
import { isMachineIntentName } from '@/core/session/actionSummary'
import { isRecoveryImplemented, type IssueView, type RecoveryKind } from '@/core/session/contracts'
import type { Degradation, PresenceSnapshot } from '@/core/presence/presence'
import type { FontScalePref } from '@/core/settings/store'
import { Button } from '@/ui/Button'
import { Icon, iconRuntimeAvailable } from '@/ui/Icon'
import { SheetPanel } from '@/ui/Sheet'
import { RADIUS, TARGET, TYPE, scale, textStyle } from '@/ui/tokens'
import type { Palette } from '@/ui/theme'

import { dockLabelMode } from './dockLabel'

export interface FocusDockProps {
  p: Palette
  fontScale: FontScalePref
  snapshot: PresenceSnapshot
  onConfirm(reply: '确认' | '取消', operationId?: string): void
  /** 显式补槽回复（AR05 §4.2）：指定 operationId + 这一次回答的值，走正常请求链。
   *  没接这个回调时补槽卡只显示、不给可点的建议值——**不造点了没反应的按钮**。 */
  onSlotReply?(operationId: string, value: string): void
  onCancelTurn(): void
  onReenableBargeIn?(): void
  /** 服务端下发的结构化问题（AR05 §5.1）。与 `degradation`（客户端设备事实）**分开渲染**：
   *  一个是「服务端说这轮出了什么事」，一个是「这台设备此刻什么状态」，混成一行就分不清
   *  该去要授权还是该去开权限。 */
  issues?: readonly IssueView[]
  /** 恢复动作出口。只有客户端真的实现了的 kind 才会被调用（见 isRecoveryImplemented）。 */
  onIssueAction?(kind: RecoveryKind, issue: IssueView): void
  expanded?: boolean
  onExpandedChange?(expanded: boolean): void
}

function fmt(ms: number): string {
  const s = Math.ceil(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s}s`
}

/** 有出口的降级才进 Dock；transport_unknown / recoverable_error 由气泡与胶囊表达 */
function dockDegradations(snapshot: PresenceSnapshot): Degradation[] {
  return snapshot.degradation.filter((d) => d.kind !== 'transport_unknown' && d.kind !== 'recoverable_error')
}

/** 承诺面此刻有没有东西可画——**唯一的一份**：组件自己与根宿主（AR04 第十五节，支持页占布局空间的
 *  容器）都读它；宿主没有内容时连底部安全区都不留，不然又是一条常驻空条。 */
export function focusDockVisible(snapshot: PresenceSnapshot, issues: readonly IssueView[] = []): boolean {
  return !!pinCommitment(snapshot.commitment) || dockDegradations(snapshot).length > 0 || issues.length > 0
}

export function FocusDock(props: FocusDockProps) {
  const { p, fontScale, snapshot } = props
  const pinned = pinCommitment(snapshot.commitment)
  const degradations = dockDegradations(snapshot)
  const issues = props.issues ?? []
  if (!pinned && !degradations.length && !issues.length) return null
  const solid = p.surfaceHigh
  return (
    <View testID="focus-dock" style={{ paddingHorizontal: 12, paddingBottom: 6, gap: 6 }}>
      {pinned ? <Commitments {...props} pinned={pinned} solid={solid} /> : null}
      {issues.map((issue) => (
        <IssueRow
          key={`${issue.code}:${issue.operationId}:${issue.requestId}`}
          p={p}
          fontScale={fontScale}
          driving={snapshot.driving}
          issue={issue}
          solid={solid}
          onAction={props.onIssueAction}
        />
      ))}
      {degradations.map((d) => (
        // key 带上区分维：同一种 kind 上游今天最多 push 一次，但 mic + camera 两个
        // permission_denied 是随时会出现的形态，那时 `key={d.kind}` 就是 React key 冲突
        <DegradationRow
          key={`${d.kind}:${'what' in d ? d.what : 'reason' in d ? d.reason : ''}`}
          p={p}
          fontScale={fontScale}
          driving={props.snapshot.driving}
          d={d}
          solid={solid}
          onReenableBargeIn={props.onReenableBargeIn}
        />
      ))}
    </View>
  )
}

/** 所有承诺清空时本组件卸载，下一组承诺不会继承上一次打开的列表。 */
function Commitments(props: FocusDockProps & {
  pinned: NonNullable<ReturnType<typeof pinCommitment>>
  solid: string
}) {
  const [localExpanded, setLocalExpanded] = useState(false)
  const expanded = props.expanded ?? localExpanded
  const setExpanded = props.onExpandedChange ?? setLocalExpanded
  const { p, fontScale, snapshot, pinned } = props
  const h = scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  return (
    <>
      <CommitmentCard {...props} item={pinned.item} />
      {pinned.others > 0 ? (
        // Figma D-1：「另有 N 个待处理」在卡片外、下方靠右，不撑高钉住的那张卡
        <Pressable
          testID="dock-others"
          onPress={() => setExpanded(true)}
          accessibilityRole="button"
          style={{ minHeight: h, flexDirection: 'row', alignItems: 'center', gap: 2, alignSelf: 'flex-end', paddingHorizontal: 4 }}
        >
          <Text style={[textStyle('caption', fontScale), { color: p.fg3 }]}>另有 {pinned.others} 个待处理{iconRuntimeAvailable() ? '' : ' ›'}</Text>
          {iconRuntimeAvailable() ? <Icon name="chevron-right" size={14} color={p.fg3} /> : null}
        </Pressable>
      ) : null}
      {expanded ? (
        <Modal transparent animationType="fade" onRequestClose={() => setExpanded(false)}>
          <SafeAreaView edges={['top']} style={{ flex: 1, justifyContent: 'flex-end', backgroundColor: p.scrim }}>
            <View accessibilityViewIsModal style={{ maxHeight: '85%' }}>
              <SheetPanel
                p={p}
                fontScale={fontScale}
                title={`待处理事项（${snapshot.commitment.length}）`}
                onClose={() => setExpanded(false)}
                closeLabel="关闭待处理列表"
                closeTestID="dock-list-close"
                style={{ maxHeight: '100%' }}
              >
                <ScrollView testID="dock-list" contentContainerStyle={{ gap: 10, paddingBottom: 8 }}>
                  {snapshot.commitment.map((item) => (
                    <CommitmentCard {...props} key={`${item.kind}:${item.id}`} item={item} testIdPrefix={`dock-list-${item.id}`} />
                  ))}
                </ScrollView>
              </SheetPanel>
            </View>
          </SafeAreaView>
        </Modal>
      ) : null}
    </>
  )
}

function CommitmentCard({
  p,
  fontScale,
  snapshot,
  item,
  solid,
  onConfirm,
  onSlotReply,
  onCancelTurn,
  testIdPrefix = 'dock',
}: FocusDockProps & { item: DockItem; solid: string; testIdPrefix?: string }) {
  // **时钟只有一个**：`usePresence` 已经在每秒 tick，`snapshot.now` 是那一份的读数。
  // 这里曾经自己起过一份 `setInterval`，而它在生产路径上是冻的——`derivePresence` 每秒现造
  // 新的 `DockItem`，`useEffect(…, [item])` 依赖的是对象引用 ⇒ 每秒 cleanup + 重建，本地
  // now 停在挂载那一刻。状态画廊里它反而会走（静态 snapshot、引用稳定）：**取证屏与生产
  // 路径在这一点上的输入形态相反，画廊绿证明不了生产绿**（第 2 批坑⑤）。
  const now = snapshot.now
  // B4-11 §6「目标 ≥56dp」：行车 56 / 泊车 48（TARGET 是唯一的一份，不在这里写数）
  const h = scale(snapshot.driving ? TARGET.driving : TARGET.parked, 'target', fontScale)
  const border = item.kind === 'confirm' ? p.amberLine : p.line
  // 右侧标签的让位（评审 ❌-1）：200% 字号下它随标题同比放大、把标题挤成「这..」。
  // 隐藏时把分类并进标题的读屏 label，信息不丢，只是不再抢那一行。
  const { fontScale: sysScale } = useWindowDimensions()
  const labelMode = dockLabelMode(fontScale, sysScale)
  // 风险档取服务端事实：high 才说「危险动作」。策略不可信时先说清楚这一条不能确认。
  const kindLabel =
    item.kind === 'confirm'
      ? item.subkind === 'location'
        ? '位置授权'
        : item.policyBroken
          ? '策略未取到 · 暂不能确认'
          : item.risk === 'low'
            ? '需要你确认'
            : '危险动作 · 需二次确认'
      : item.kind === 'slot'
        ? item.state === 'held'
          ? '待补充 · 已搁置'
          : '待补充'
        : ''
  // 打磨批 A（评审 P10 / V5）第二道防线：标题**不许是机器意图名**（`trunk.open`）。正路是
  // commitmentTitle 回落原话与服务端出中文摘要（批 G）；两条都没兜住时这里给人话标题、原文降为说明行。
  // 判据只有 actionSummary.ts 那一份正则。
  const machineName = item.kind === 'confirm' && isMachineIntentName(item.summary)
  const title = item.kind === 'confirm' ? (machineName ? '待确认的车辆操作' : item.summary) : ''
  return (
    // ⚠ `accessibilityLiveRegion` **不在这一层**：这个子树里有每秒变的倒计时，挂在根上会让
    // TalkBack 每秒重播整张卡。live region 只挂在下面那些「内容变了才该播一次」的摘要行上。
    <View
      testID={`${testIdPrefix}-${item.kind}`}
      style={{
        backgroundColor: solid,
        borderRadius: RADIUS.lg,
        borderWidth: 1,
        borderColor: border,
        paddingHorizontal: 16,
        paddingVertical: 12,
        gap: 10,
        boxShadow: p.elev2,
      }}
    >
      {item.kind === 'confirm' ? (
        <>
          <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {/* 打磨批 C（评审 P15）：线性图标替 emoji；svg 原生缺席回退文字（Icon.tsx 既有判据） */}
            {iconRuntimeAvailable() ? (
              <Icon name={item.subkind === 'location' ? 'location' : 'warning'} size={20} color={p.amber} />
            ) : (
              <Text style={{ color: p.amber, fontSize: scale(TYPE.body, 'text', fontScale) }}>!</Text>
            )}
            <Text
              testID="dock-title"
              numberOfLines={2}
              accessibilityLabel={labelMode === 'hidden' ? `${kindLabel}：${title}` : undefined}
              style={[textStyle('titleM', fontScale), { color: p.fg1, flex: 1, flexShrink: 1 }]}
            >
              {title}
            </Text>
            {labelMode === 'full' ? (
              <Text style={[textStyle('caption', fontScale), { color: p.fg3, flexShrink: 0 }]}>{kindLabel}</Text>
            ) : null}
          </View>
          {machineName ? (
            <Text testID="dock-title-detail" numberOfLines={1} style={{ color: p.fg3, fontSize: scale(TYPE.micro, 'text', fontScale), fontFamily: TYPE.mono }}>
              {item.summary}
            </Text>
          ) : null}
          {item.subkind !== 'location' ? (
            <View style={{ gap: 4 }}>
              <View style={{ height: 3, borderRadius: 2, backgroundColor: p.lineStrong, overflow: 'hidden' }}>
                <View
                  style={{
                    height: 3,
                    // 分母取**服务端窗口**，没有才回落本地 TTL：服务端说 60s 而分母写死
                    // 300s 时，进度条一上来就只剩 1/5，看着像马上要过期
                    width: `${Math.min(100, Math.round((confirmRemainingMs(item, now) / (item.windowMs && item.windowMs > 0 ? item.windowMs : PENDING_TTL_MS)) * 100))}%`,
                    backgroundColor: p.amber,
                  }}
                />
              </View>
              <Text testID={`${testIdPrefix}-countdown`} style={[textStyle('caption', fontScale), { color: p.fg3 }]}>
                {fmt(confirmRemainingMs(item, now))} 后过期
              </Text>
            </View>
          ) : null}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Pressable
              testID={`${testIdPrefix}-cancel`}
              accessibilityRole="button"
              onPress={() => onConfirm('取消', item.subkind === 'location' ? undefined : item.id)}
              style={{ flex: 1, minHeight: h, borderRadius: RADIUS.full, backgroundColor: p.surfaceHighest, alignItems: 'center', justifyContent: 'center' }}
            >
              <Text style={[textStyle('labelL', fontScale), { color: p.fg1 }]}>{item.subkind === 'location' ? '拒绝' : '取消'}</Text>
            </Pressable>
            {item.policyBroken ? (
              // 策略缺失/畸形 ⇒ **不给确认入口**（方案 §7.1：不能默认为允许）。
              // 取消与重新发起仍可达——停手不等于把用户困住。
              <View
                testID={`${testIdPrefix}-policy-broken`}
                style={{ flex: 2, minHeight: h, borderRadius: RADIUS.full, borderWidth: 1, borderStyle: 'dashed', borderColor: p.lineStrong, backgroundColor: p.surfaceLow, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 12 }}
              >
                <Text style={[textStyle('caption', fontScale), { color: p.fg2, textAlign: 'center' }]}>
                  确认策略没取到，请重新发起
                </Text>
              </View>
            ) : (
              <Pressable
                testID={`${testIdPrefix}-accept`}
                accessibilityRole="button"
                onPress={() => onConfirm('确认', item.subkind === 'location' ? undefined : item.id)}
                style={{ flex: 2, minHeight: h, borderRadius: RADIUS.full, backgroundColor: p.amber, alignItems: 'center', justifyContent: 'center' }}
              >
                <Text style={[textStyle('labelL', fontScale), { color: p.onAmber }]}>{item.subkind === 'location' ? '允许' : '确认'}</Text>
              </Pressable>
            )}
          </View>
        </>
      ) : item.kind === 'task' ? (
        <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {iconRuntimeAvailable() ? <Icon name="refresh" size={18} color={p.teal} /> : <Text style={{ color: p.teal, fontSize: scale(TYPE.body, 'text', fontScale) }}>…</Text>}
          <Text numberOfLines={1} style={[textStyle('bodyM', fontScale), { color: p.fg1, flex: 1 }]}>{item.label}…</Text>
          <Pressable accessibilityRole="button" onPress={onCancelTurn} style={{ minHeight: h, paddingHorizontal: 12, justifyContent: 'center' }}>
            <Text style={[textStyle('labelL', fontScale), { color: p.amber }]}>取消</Text>
          </Pressable>
        </View>
      ) : item.kind === 'queue' ? (
        <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {iconRuntimeAvailable() ? <Icon name="clock" size={18} color={p.fg3} /> : null}
          <Text style={[textStyle('bodyM', fontScale), { color: p.fg2, flex: 1 }]}>{item.count} 条消息排队中，连上后自动补发</Text>
        </View>
      ) : (
        <View style={{ gap: 8 }}>
          <View accessibilityLiveRegion="assertive" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {iconRuntimeAvailable() ? <Icon name="info" size={20} color={p.accent} /> : null}
            <Text
              numberOfLines={2}
              accessibilityLabel={labelMode === 'hidden' ? `${kindLabel}：还差一个信息：${item.missing}` : undefined}
              style={[textStyle('titleM', fontScale), { color: p.fg1, flex: 1 }]}
            >
              还差一个信息：{item.missing}
            </Text>
            {labelMode === 'full' ? (
              <Text style={[textStyle('caption', fontScale), { color: p.fg3, flexShrink: 0 }]}>{kindLabel}</Text>
            ) : null}
          </View>
          {item.expiresAt > 0 ? (
            <Text testID={`${testIdPrefix}-slot-countdown`} style={[textStyle('caption', fontScale), { color: p.fg3 }]}>
              {fmt(Math.max(0, item.expiresAt - now))} 后过期
            </Text>
          ) : null}
          {item.suggestions.length > 0 && onSlotReply ? (
            // 建议值**只来自本次真实 Agent 结果/候选集**（服务端保证）。没有建议值时
            // 这一段整个不渲染：给自由输入，不造可点的假选项（方案 §4.2）。
            <View testID={`${testIdPrefix}-slot-suggestions`} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {item.suggestions.map((value) => (
                <Pressable
                  key={value}
                  testID={`${testIdPrefix}-slot-suggestion-${value}`}
                  accessibilityRole="button"
                  accessibilityLabel={`用「${value}」回答${item.missing}`}
                  onPress={() => onSlotReply(item.id, value)}
                  style={{ minHeight: h, paddingHorizontal: 16, justifyContent: 'center', borderRadius: RADIUS.full, borderWidth: 1, borderColor: p.line, backgroundColor: p.surfaceHighest }}
                >
                  <Text style={[textStyle('labelL', fontScale), { color: p.fg1 }]}>{value}</Text>
                </Pressable>
              ))}
            </View>
          ) : (
            <Text style={[textStyle('caption', fontScale), { color: p.fg2 }]}>
              直接说或输入都可以
            </Text>
          )}
        </View>
      )}
    </View>
  )
}

/** 恢复动作的按钮文案兜底：服务端给了 label 就用它（它更贴本次场景）。 */
const RECOVERY_FALLBACK_LABEL: Record<RecoveryKind, string> = {
  open_capability_settings: '查看能力与连接',
  open_voice_settings: '语音设置',
  reconfigure_connection: '重新配置连接',
  retry_request: '换个说法再试',
  replay_audio: '重播',
  dismiss: '知道了',
}

function IssueRow({
  p,
  fontScale,
  driving,
  issue,
  solid,
  onAction,
}: {
  p: Palette
  fontScale: FontScalePref
  driving: boolean
  issue: IssueView
  solid: string
  onAction?(kind: RecoveryKind, issue: IssueView): void
}) {
  // **只渲染客户端真的实现了的 kind**：契约里定义了六个，App 现在能兑现五个；
  // 把没实现的画成按钮就是承诺了做不到的事（比不给入口更糟）。
  const actions = onAction
    ? issue.recovery.filter((r) => isRecoveryImplemented(r.kind))
    : []
  const danger = issue.severity === 'error'
  return (
    <View
      testID={`dock-issue-${issue.code}`}
      style={{
        backgroundColor: solid,
        borderRadius: RADIUS.lg,
        borderWidth: 1,
        borderColor: danger ? p.red : p.line,
        paddingHorizontal: 16,
        paddingVertical: 12,
        gap: 10,
        boxShadow: p.elev2,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8 }}>
        {iconRuntimeAvailable() ? <Icon name={danger ? 'warning' : 'info'} size={20} color={danger ? p.red : p.fg2} /> : null}
        <Text accessibilityLiveRegion="polite" style={[textStyle('bodyM', fontScale), { color: p.fg1, flex: 1 }]}>
          {issue.message}
        </Text>
      </View>
      {actions.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, justifyContent: 'flex-end' }}>
          {actions.map((r) => (
            <Button
              key={r.kind}
              p={p}
              variant="outlined"
              testID={`dock-issue-${issue.code}-${r.kind}`}
              label={r.label || RECOVERY_FALLBACK_LABEL[r.kind]}
              driving={driving}
              fontScale={fontScale}
              onPress={() => onAction?.(r.kind, issue)}
            />
          ))}
        </View>
      ) : null}
    </View>
  )
}

const DEGRADATION_TEXT: Record<Exclude<Degradation['kind'], 'transport_unknown' | 'recoverable_error'>, (d: Degradation) => string> = {
  permission_denied: (d) => (d.kind === 'permission_denied' ? d.text : ''),
  service_degraded: (d) => (d.kind === 'service_degraded' ? d.text : ''),
  safety_blocked: (d) => (d.kind === 'safety_blocked' ? d.text : ''),
  // AR03：这句原来指着一个**不存在**的控件（B5 撤掉层内停止键之后）。现在「停止播报」真的有了，
  // 但停播会把免唤醒收回待机（不开续问窗，见 voiceLoop.stopSpeaking）⇒ 后半句也要跟着说实话：
  // 停完要重新唤醒，不是接着说就行。
  audio_echo_degraded: () => '环境回声较强，本轮已关闭插话；点「停止播报」后重新唤醒再问',
  fatal: (d) => (d.kind === 'fatal' ? d.text : ''),
}

function DegradationRow({
  p,
  fontScale,
  driving,
  d,
  solid,
  onReenableBargeIn,
}: {
  p: Palette
  fontScale: FontScalePref
  driving: boolean
  d: Degradation
  solid: string
  onReenableBargeIn?(): void
}) {
  if (d.kind === 'transport_unknown' || d.kind === 'recoverable_error') return null
  const text = DEGRADATION_TEXT[d.kind](d)
  const action =
    d.kind === 'permission_denied'
      ? { label: '去系统设置', run: () => void Linking.openSettings() }
      : d.kind === 'audio_echo_degraded' && onReenableBargeIn
        ? { label: '重新开启插话', run: onReenableBargeIn }
        : null
  return (
    <View
      testID={`dock-${d.kind}`}
      style={{ backgroundColor: solid, borderRadius: RADIUS.lg, borderWidth: 1, borderColor: d.kind === 'safety_blocked' || d.kind === 'fatal' ? p.red : p.line, paddingHorizontal: 16, paddingVertical: 12, gap: 6, boxShadow: p.elev2 }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 8 }}>
        {iconRuntimeAvailable() ? (
          <Icon name={d.kind === 'permission_denied' ? 'voice-input' : d.kind === 'audio_echo_degraded' ? 'voice-output' : 'info'} size={18} color={p.fg2} />
        ) : null}
        <Text style={[textStyle('bodyM', fontScale), { color: p.fg2, flex: 1 }]}>{text}</Text>
      </View>
      {action ? (
        <Pressable accessibilityRole="button" onPress={action.run} style={{ minHeight: scale(driving ? TARGET.driving : TARGET.parked, 'target', fontScale), justifyContent: 'center', paddingHorizontal: 8, alignSelf: 'flex-end' }}>
          <Text style={[textStyle('labelL', fontScale), { color: p.accent }]}>{action.label}</Text>
        </Pressable>
      ) : null}
    </View>
  )
}
