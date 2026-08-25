// 服务器配置（实施计划 M0-5）。token 只存 expo-secure-store（Android Keystore），
// 不落 AsyncStorage、不进日志；其余字段 AsyncStorage。
export type ServerPreset = 'cloud' | 'lan' | 'custom'

export interface ServerConfig {
  preset: ServerPreset
  /** preset='cloud' 时的 Tailnet FQDN（两条 URL 由它派生） */
  fqdn?: string
  /** 主链入口，如 https://{fqdn}:8443 */
  edgeUrl: string
  /** 音频面入口，如 https://{fqdn}:8444 */
  audioUrl: string
  /** AUTH_TOKENS 条目的 token 段（存 SecureStore） */
  token: string
}
