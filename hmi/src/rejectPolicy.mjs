// R4.4 P2：连续云端拒识 → 聆听收紧策略（纯逻辑、node 可测；controller 只接线动作）。
// 嘈杂是环境状态不是单句事件，端侧要有会话级策略：连续被云端语义拒识时逐级收紧续问窗，
// 直到「仅唤醒词模式」，一次成功交互即复位。本地字面判据（filler/短语音）**不计入**——
// 那些本就没上云，只有云端语义拒识才 bump（避免安静环境「嗯」两声就被收紧，母卡 D4）。
//
// 动作（controller 消费）：
//   null                                   无动作（首次拒识只计数）
//   {type:'tighten', followupMs}           续问窗减半 + notice
//   {type:'wake_only'}                     降级仅唤醒词（续问窗=0 + 关 VAD barge-in）+ notice
//   {type:'restore', followupMs}           复位（还原续问窗 + 开 VAD barge-in）

export class RejectPolicy {
  constructor({ baseFollowupMs = 8000 } = {}) {
    this._base = baseFollowupMs
    this._streak = 0
  }

  /** 用户设置变化时同步基准续问窗（restore/tighten 都以最新设置为准，母卡 D4 备注）。 */
  setBaseFollowupMs(ms) {
    this._base = Math.max(0, ms | 0)
  }

  get streak() {
    return this._streak
  }

  /** 云端语义拒识一次。返回本次应采取的收紧动作（阈值查表）。 */
  onRejected() {
    this._streak += 1
    if (this._streak === 2) return { type: 'tighten', followupMs: Math.round(this._base / 2) }
    if (this._streak >= 3) return { type: 'wake_only' }
    return null // 第 1 次只计数
  }

  /** 任一轮正常处理（非拒识 final）。有连续拒识则复位，否则无动作。 */
  onAccepted() {
    if (this._streak > 0) {
      this._streak = 0
      return { type: 'restore', followupMs: this._base }
    }
    return null
  }
}
