// 服务器配置（实施计划 M0-5）。整份配置（含 token）一个键存 expo-secure-store（Android Keystore），
// 不落 AsyncStorage、不进日志（storage.ts 头注：地址与凭据必须一次写入，不能失去绑定）。
export type ServerPreset = 'cloud' | 'lan' | 'custom'

export interface ServerConfig {
  preset: ServerPreset
  /** preset='cloud' 时的 Tailnet FQDN（两条 URL 由它派生） */
  fqdn?: string
  /** 主链入口，如 https://{fqdn}:8443 */
  edgeUrl: string
  /** 音频面入口，如 https://{fqdn}:8444 */
  audioUrl: string
  /** AUTH_TOKENS 条目的 token 段（与其余字段同一个 SecureStore 键） */
  token: string
}
