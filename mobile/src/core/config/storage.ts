// 配置持久化（实施计划 M0-5）：token → expo-secure-store（Android Keystore），
// 其余 → AsyncStorage。两处任一缺失按「未配置」处理（fail-closed，引导页重配）。
import AsyncStorage from '@react-native-async-storage/async-storage'
import * as SecureStore from 'expo-secure-store'
import type { ServerConfig } from './types'

const CONFIG_KEY = 'xiaozhou.server-config.v1'
const TOKEN_KEY = 'xiaozhou.server-token.v1'

export async function loadServerConfig(): Promise<ServerConfig | null> {
  try {
    const raw = await AsyncStorage.getItem(CONFIG_KEY)
    if (!raw) return null
    const base = JSON.parse(raw) as Omit<ServerConfig, 'token'>
    if (!base || typeof base.edgeUrl !== 'string' || !base.edgeUrl) return null
    const token = (await SecureStore.getItemAsync(TOKEN_KEY)) ?? ''
    if (!token) return null
    return { ...base, token }
  } catch {
    return null
  }
}

export async function saveServerConfig(cfg: ServerConfig): Promise<void> {
  const { token, ...base } = cfg
  await SecureStore.setItemAsync(TOKEN_KEY, token)
  await AsyncStorage.setItem(CONFIG_KEY, JSON.stringify(base))
}

export async function clearServerConfig(): Promise<void> {
  await AsyncStorage.removeItem(CONFIG_KEY)
  await SecureStore.deleteItemAsync(TOKEN_KEY)
}
