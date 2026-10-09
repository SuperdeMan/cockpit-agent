import type { IconName } from './components/ui/Icon'

export type OutcomeTone = 'good' | 'pending' | 'warn' | 'critical' | 'neutral'
export interface OutcomePresentation { label: string; tone: OutcomeTone; icon?: IconName }
export interface OutcomeTurn { outcome?: string | null; outcome_category?: string | null; status?: string | null }

// Display only. runtime/outcome.py owns categorization; never infer a category here.
// Figma Foundations F-02 + the approved brief, appendix C.
export const OUTCOME_DISPLAY: Record<string, OutcomePresentation> = {
  completed: { label: '已完成', tone: 'good', icon: 'check-circle' },
  partial: { label: '部分完成', tone: 'warn', icon: 'half-circle' },
  uncertain: { label: '结果未定', tone: 'warn', icon: 'half-circle' },
  pending_confirm: { label: '待确认', tone: 'pending', icon: 'clock' },
  pending_slot: { label: '待补充', tone: 'pending', icon: 'clock' },
  planner_failure: { label: '规划失败', tone: 'critical', icon: 'x-circle' },
  failed: { label: '执行失败', tone: 'critical', icon: 'x-circle' },
  stream_lost: { label: '结果丢失', tone: 'critical', icon: 'x-circle' },
  escalate_failed: { label: '改派失败', tone: 'critical', icon: 'x-circle' },
  store_unavailable: { label: '会话状态不可用', tone: 'critical', icon: 'x-circle' },
  pending_unavailable: { label: '挂起表不可用', tone: 'critical', icon: 'x-circle' },
  memory_unavailable: { label: '记忆不可用', tone: 'critical', icon: 'x-circle' },
  clarify: { label: '澄清中', tone: 'warn', icon: 'help-circle' },
  unresolved_object: { label: '对象未定', tone: 'warn', icon: 'help-circle' },
  candidate_missing: { label: '无候选', tone: 'warn', icon: 'help-circle' },
  cancel_unresolved: { label: '取消无对象', tone: 'warn', icon: 'help-circle' },
  no_plan: { label: '无计划', tone: 'warn', icon: 'ban' },
  unsupported: { label: '不支持', tone: 'warn', icon: 'ban' },
  permission_missing: { label: '缺权限', tone: 'warn', icon: 'lock' },
  memory_off: { label: '记忆未开', tone: 'warn', icon: 'lock' },
  context_invalidated: { label: '上下文已失效', tone: 'warn', icon: 'lock' },
  safety_origin_blocked: { label: '安全拦截', tone: 'warn', icon: 'shield' },
  injection_rejected: { label: '注入拦截', tone: 'warn', icon: 'shield' },
  not_addressed: { label: '非受话', tone: 'neutral', icon: 'mic-off' },
  cancelled: { label: '已取消', tone: 'neutral', icon: 'rotate-ccw' },
  cancel_unconfirmed: { label: '取消未落实', tone: 'neutral', icon: 'rotate-ccw' },
  pending_kept: { label: '保留挂起', tone: 'neutral', icon: 'rotate-ccw' },
  pending_mismatch: { label: '确认对不上', tone: 'neutral', icon: 'rotate-ccw' },
  pending_expired: { label: '挂起已过期', tone: 'neutral', icon: 'rotate-ccw' },
  pending_missing: { label: '挂起不存在', tone: 'neutral', icon: 'rotate-ccw' },
  pending_claimed: { label: '已被他轮处理', tone: 'neutral', icon: 'rotate-ccw' },
  pending_ambiguous: { label: '多条待确认', tone: 'neutral', icon: 'rotate-ccw' },
  pending_asked: { label: '复述挂起', tone: 'neutral', icon: 'rotate-ccw' },
  no_pending: { label: '无待确认', tone: 'neutral', icon: 'rotate-ccw' },
  store_fenced: { label: '删除栅栏中', tone: 'neutral', icon: 'rotate-ccw' },
  fact_answered: { label: '事实直答', tone: 'neutral', icon: 'rotate-ccw' },
  constraint_noted: { label: '偏好已登记', tone: 'neutral', icon: 'rotate-ccw' },
}

export const OUTCOME_CATEGORY_DISPLAY: Record<string, OutcomePresentation> = {
  progress: { label: '进度', tone: 'neutral', icon: 'half-circle' },
  dependency_or_planner_failure: { label: '服务或规划失败', tone: 'critical', icon: 'x-circle' },
  ambiguous: { label: '理解未定', tone: 'warn', icon: 'help-circle' },
  unsupported: { label: '能力不支持', tone: 'warn', icon: 'ban' },
  permission_missing: { label: '缺权限', tone: 'warn', icon: 'lock' },
  policy_blocked: { label: '策略拦截', tone: 'warn', icon: 'shield' },
  not_addressed: { label: '非受话', tone: 'neutral', icon: 'mic-off' },
  session_control: { label: '会话控制', tone: 'neutral', icon: 'rotate-ccw' },
}

const STATUS_DISPLAY: Record<string, OutcomePresentation> = {
  ok: { label: 'ok', tone: 'neutral' },
  need_confirm: { label: '待确认', tone: 'pending', icon: 'clock' },
  err: { label: '出错', tone: 'critical', icon: 'x-circle' },
  error: { label: '出错', tone: 'critical', icon: 'x-circle' },
  timeout: { label: '超时', tone: 'critical', icon: 'x-circle' },
  empty: { label: '空结果', tone: 'critical', icon: 'x-circle' },
}

export function outcomeDisplay(turn: OutcomeTurn): OutcomePresentation {
  if (turn.outcome) {
    const known = Object.prototype.hasOwnProperty.call(OUTCOME_DISPLAY, turn.outcome) ? OUTCOME_DISPLAY[turn.outcome] : undefined
    if (known) return known
    const category = Object.prototype.hasOwnProperty.call(OUTCOME_CATEGORY_DISPLAY, turn.outcome_category ?? '')
      ? OUTCOME_CATEGORY_DISPLAY[turn.outcome_category ?? ''] : undefined
    return { label: turn.outcome, tone: category?.tone ?? 'neutral', icon: category?.icon }
  }
  const status = turn.status || '—'
  const display = (Object.prototype.hasOwnProperty.call(STATUS_DISPLAY, status) ? STATUS_DISPLAY[status] : undefined)
    ?? { label: status, tone: 'neutral' as const }
  return { ...display, label: `${display.label} · 无账本` }
}
