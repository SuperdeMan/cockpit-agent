// mobile/src/core/api/sessionInfo.ts
// 会话身份与能力摘要（AR05 §6.1 / R14）：`GET {edgeUrl}/api/session`。
//
// 在这之前 App 端**判不了 token 的 scope**（不透明串、没有查询端点），于是设置页只能
// 拿 token 尾 4 位当用户名、拿设备角色推「不控车」——一个是假身份，一个是假权限。
// 现在服务端有了只读查询面，这里把它取回来。
//
// 四种失败必须分得开（方案 §6.3）：
//  · 明确认证拒绝（401）→ token 真的不行，停自动重连、走重新配置；
//  · 旧服务端（404 / 没有这个端点）→ legacy，沿用既有确认流程，不推断结构化能力；
//  · 网络/超时 → 未知，标待刷新；**绝不据此判 token 失效**；
//  · 服务端说自己只取到一半（summary_status=partial）→ 显示未知，不谎称完整。
//
// token 只放 Authorization 头，不进 URL——URL 会进访问日志。
export type SessionSummaryStatus = 'complete' | 'partial'

export interface CapabilityStatusView {
  id: string
  displayName: string
  /** available | unauthorized | unavailable | unknown；未知值原样保留，UI 显示为未知 */
  status: string
  reasonCode: string
}

export interface SessionSummary {
  contractVersion: string
  authenticated: boolean
  userId: string
  vehicleId: string
  /** token | poc_default | fail_closed —— PoC 默认放行与明确授权不是一回事 */
  authorizationSource: string
  grantedScopes: string[]
  capabilities: CapabilityStatusView[]
  generatedAtMs: number
  expiresAtMs: number
  summaryStatus: SessionSummaryStatus
  summaryReason: string
}

export type SessionInfoResult =
  | { kind: 'ok'; summary: SessionSummary }
  /** 服务端明确拒绝这个 token */
  | { kind: 'unauthorized' }
  /** 旧服务端：没有这个端点 */
  | { kind: 'legacy' }
  /** 网络/超时/服务端暂时不可用——**不是** token 失效 */
  | { kind: 'unknown'; reason: string }

const TIMEOUT_MS = 8000

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const list = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && !!x) : []

export function sessionInfoUrl(edgeUrl: string): string {
  return `${edgeUrl.replace(/\/+$/, '')}/api/session`
}

export function parseSessionSummary(raw: unknown): SessionSummary {
  const d = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const caps = Array.isArray(d.capabilities) ? d.capabilities : []
  const status = str(d.summary_status)
  return {
    contractVersion: str(d.contract_version),
    authenticated: d.authenticated === true,
    userId: str(d.user_id),
    vehicleId: str(d.vehicle_id),
    authorizationSource: str(d.authorization_source),
    grantedScopes: list(d.granted_scopes),
    capabilities: caps
      .filter((c): c is Record<string, unknown> => !!c && typeof c === 'object')
      .map((c) => ({
        id: str(c.id),
        displayName: str(c.display_name) || str(c.id),
        status: str(c.status) || 'unknown',
        reasonCode: str(c.reason_code),
      }))
      .filter((c) => !!c.id),
    generatedAtMs: num(d.generated_at_ms),
    expiresAtMs: num(d.expires_at_ms),
    // 空 = 旧服务端没有这一位，按 complete 处理（逐字回落到本字段诞生前）
    summaryStatus: status === 'partial' ? 'partial' : 'complete',
    summaryReason: str(d.summary_reason),
  }
}

/** 摘要过期了吗——过期后 UI 标「待刷新」，**不得用缓存授权执行**。 */
export function summaryStale(summary: SessionSummary, now = Date.now()): boolean {
  return summary.expiresAtMs > 0 && now >= summary.expiresAtMs
}

export async function fetchSessionInfo(
  edgeUrl: string,
  token: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SessionInfoResult> {
  if (!edgeUrl) return { kind: 'unknown', reason: '未配置服务器' }
  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = controller ? setTimeout(() => controller.abort(), TIMEOUT_MS) : null
  try {
    const res = await fetchImpl(sessionInfoUrl(edgeUrl), {
      method: 'GET',
      // token 只走 Authorization 头：URL 会进访问日志，凭证不该出现在那里
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      ...(controller ? { signal: controller.signal } : {}),
    })
    if (res.status === 401 || res.status === 403) return { kind: 'unauthorized' }
    if (res.status === 404) return { kind: 'legacy' }
    if (!res.ok) return { kind: 'unknown', reason: `HTTP ${res.status}` }
    return { kind: 'ok', summary: parseSessionSummary(await res.json()) }
  } catch (e) {
    // 超时/断网/TLS 失败都落这里。**不是**鉴权拒绝——把它判成 token 无效会让用户
    // 在飞机上重配一遍完全正确的配置（方案 §6.3）。
    return { kind: 'unknown', reason: String((e as Error)?.message || e) }
  } finally {
    if (timer) clearTimeout(timer)
  }
}
