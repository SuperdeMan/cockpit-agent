// 云端点派生与校验（实施计划 M0-5）：与 scripts/dev_stack_lib.py:346-365
// （cloud_endpoints）同构——判据三条：整串正则 + 总长 ≤253 + 每个 label ≤63 且合法。
// 纯函数零依赖（appendToken 除外，来自共享白名单），单测 test/endpoints.test.ts。
import { appendToken } from '@shared/ws.mjs'

export const TAILNET_FQDN_RE = /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.ts\.net$/
const FQDN_LABEL_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/

export function isValidTailnetFqdn(fqdn: string): boolean {
  if (!TAILNET_FQDN_RE.test(fqdn) || fqdn.length > 253) return false
  return fqdn.split('.').every((label) => label.length <= 63 && FQDN_LABEL_RE.test(label))
}

export interface CloudEndpoints {
  edgeUrl: string
  audioUrl: string
}

export function cloudEndpoints(fqdn: string): CloudEndpoints {
  if (!isValidTailnetFqdn(fqdn)) throw new Error('TAILNET_FQDN is missing or invalid')
  return { edgeUrl: `https://${fqdn}:8443`, audioUrl: `https://${fqdn}:8444` }
}

// 主链 WS 入口（§2.1）：edge http(s)→ws(s) + /ws?token=；token 拼接复用共享 appendToken
// （edge-gateway 在 upgrade 前校验 token，坏 token 直接握手失败）。
export function edgeWsUrl(edgeUrl: string, token: string): string {
  return appendToken(edgeUrl.replace(/^http/, 'ws') + '/ws', token)
}
