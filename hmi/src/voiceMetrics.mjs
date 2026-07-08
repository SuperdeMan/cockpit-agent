// R4.3b P3 语音回路指标——最小实现（主卡承诺未接线的 obs 指标）。
// HMI 无 NATS 通路，且不想为「计数」增后端面：首版仅 localStorage 累计 + console.debug，
// 供泓舟真麦验收时对照（§9 人工验收单：首唤醒单份率/语气词零上云/端点合并/THINKING 打断）。
// 纯逻辑、storage 注入 → node 可测；量产接 NATS 时只需换 sink，不动 FSM/控制器。

const KEY = 'voiceMetrics'

// FSM 语义事件 → 指标名（主卡 §P3 承诺的 6 项）。未列出的事件忽略。
const METRIC_OF = {
  wake: 'voice_wake_count',
  false_wake_dismissed: 'voice_false_wake_dismissed',
  filler_dismissed: 'voice_filler_dismissed',
  exit_word: 'voice_exit_word_count',
  endpoint_merge: 'voice_endpoint_merge_count',
  barge_in: 'voice_barge_in_count',
  turn_cancelled: 'voice_turn_cancelled_count',
  cloud_rejected: 'voice_cloud_rejected',            // R4.4 P0：云端语义拒识计数
  reject_downgrade: 'voice_reject_downgrade',        // R4.4 P2：连续拒识降级仅唤醒词
  reject_recovered: 'voice_reject_recovered',        // R4.4 P2：一次成功交互复位
}

/** 累计一个语音事件。sink 默认 localStorage（浏览器）；node 测注入内存 storage。 */
export function bumpVoiceMetric(event, storage = defaultStorage()) {
  const name = METRIC_OF[event]
  if (!name) return
  try {
    const cur = readCounts(storage)
    cur[name] = (cur[name] || 0) + 1
    storage.setItem(KEY, JSON.stringify(cur))
    if (typeof console !== 'undefined' && console.debug) console.debug('[voice-metric]', name, cur[name])
  } catch { /* 计数不可用不影响主流程 */ }
}

/** 读当前全部计数（验收/展示用）。 */
export function readCounts(storage = defaultStorage()) {
  try {
    const raw = storage.getItem(KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

export function resetVoiceMetrics(storage = defaultStorage()) {
  try { storage.removeItem(KEY) } catch { /* ignore */ }
}

function defaultStorage() {
  if (typeof localStorage !== 'undefined') return localStorage
  // node/无 DOM 环境的空实现（避免抛错）
  return { getItem: () => null, setItem: () => {}, removeItem: () => {} }
}
