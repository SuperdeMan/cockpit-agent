// 语义打断的判定请求（2026-10-11，设计 docs/design/2026-10-11-semantic-barge-in.md §4.1）——HMI 与 Android 共用这一份约定。
//
// 播报中有人插话：先问 llm-gateway「这句是不是对助手说的」，不走对话通道（那条通道一发新请求旧回答就被取消了）。
// 回包 `addressed` 只有三种：true / false / null。null 与一切失败（网络、鉴权、超时、旧网关 404）同义：判不出——
// 调用方按今天的路径处理（停旧回答、照常发出，云端照旧判），失灵不拦人。
// 纯逻辑、fetch 可注入 → node 可测。

/** 判定接口路径（llm-gateway `/api/voice/admission`）。 */
export const BARGE_IN_JUDGE_PATH = '/api/voice/admission'

/** 判定请求的等待上限（ms）：比状态机的 6 s 判定上限略短，失败先由这里收口。 */
export const BARGE_IN_JUDGE_TIMEOUT_MS = 5500

/**
 * 问一次插话那句是不是对助手说的。
 * @param {string} apiBase llm-gateway 的 http(s) 基址（HMI 的 AUDIO_API / Android 的 audioUrl）
 * @param {{ utterance: string, previous?: string, assistantName?: string, sessionId?: string }} q
 * @param {{ token?: string, timeoutMs?: number, fetchImpl?: typeof fetch }} [opts]
 * @returns {Promise<boolean | null>}
 */
export async function judgeBargeIn(apiBase, q, opts = {}) {
  const { token = '', timeoutMs = BARGE_IN_JUDGE_TIMEOUT_MS, fetchImpl = globalThis.fetch } = opts
  const utterance = String(q?.utterance || '').trim()
  if (!apiBase || !utterance || typeof fetchImpl !== 'function') return null
  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = setTimeout(() => controller?.abort(), timeoutMs)
  try {
    const resp = await fetchImpl(apiBase.replace(/\/+$/, '') + BARGE_IN_JUDGE_PATH, {
      method: 'POST',
      // token 只走 Authorization 头：URL 会进访问日志
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({
        utterance,
        previous: String(q?.previous || ''),
        assistant_name: String(q?.assistantName || ''),
        session_id: String(q?.sessionId || ''),
      }),
      ...(controller ? { signal: controller.signal } : {}),
    })
    if (!resp || !resp.ok) return null
    const data = await resp.json()
    return typeof data?.addressed === 'boolean' ? data.addressed : null
  } catch {
    return null
  } finally {
    clearTimeout(timer)
  }
}
