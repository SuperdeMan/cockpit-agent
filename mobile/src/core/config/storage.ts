// 配置持久化（实施计划 M0-5；2026-09-19 GPT-6 评审 F03 改为单键）。
//
// 整份 `ServerConfig`（含 token）**一个键**存 expo-secure-store（Android Keystore 加密）。
// 原来拆两处（token → SecureStore、其余 → AsyncStorage）：两笔写入没有共同版本，第二笔失败或进程
// 中途被杀 = 下次加载得到「旧地址 + 新 token」，新凭据被发到旧地址。地址与凭据不能失去绑定，
// 而配置只有五个短字段（远小于 SecureStore 单值 2048B 上限）⇒ 一次写入即原子，加载只认完整对象，
// 状态只可能是「完整旧配置 / 完整新配置 / 未配置」。
//
// 旧格式（v1 两键）在首次加载时迁移：两键读齐才迁，缺一按「未配置」（与原 fail-closed 一致）；
// 先写新键再删旧键，中途被杀最多留下两份一致的配置，下次加载认新键。
import AsyncStorage from '@react-native-async-storage/async-storage'
import * as SecureStore from 'expo-secure-store'
import type { ServerConfig } from './types'

const CONFIG_KEY = 'xiaozhou.server-config.v2'
const LEGACY_CONFIG_KEY = 'xiaozhou.server-config.v1'
const LEGACY_TOKEN_KEY = 'xiaozhou.server-token.v1'
/** expo-secure-store 单值上限（超过它库只告警、不保证能读回）。字段全是 ASCII，按 UTF-16 长度量即可 */
export const SECURE_VALUE_LIMIT = 2048

const subscribers = new Set<() => void>()
export function subscribeServerConfig(fn: () => void): () => void {
  subscribers.add(fn)
  return () => { subscribers.delete(fn) }
}

function notify(): void {
  for (const fn of subscribers) fn()
}

function parseConfig(raw: string | null | undefined): ServerConfig | null {
  if (!raw) return null
  try {
    const cfg = JSON.parse(raw) as ServerConfig
    if (!cfg || typeof cfg.edgeUrl !== 'string' || !cfg.edgeUrl) return null
    if (typeof cfg.token !== 'string' || !cfg.token) return null
    return cfg
  } catch {
    return null
  }
}

/** 唯一的写入点：超上限**显式失败**，不截断（截断的 token 是另一种「混合配置」） */
async function writeConfig(cfg: ServerConfig): Promise<void> {
  const raw = JSON.stringify(cfg)
  if (raw.length > SECURE_VALUE_LIMIT) {
    throw new Error(`服务器配置超过安全存储上限（${raw.length} > ${SECURE_VALUE_LIMIT}）`)
  }
  await SecureStore.setItemAsync(CONFIG_KEY, raw)
}

async function dropLegacy(): Promise<void> {
  await AsyncStorage.removeItem(LEGACY_CONFIG_KEY).catch(() => {})
  await SecureStore.deleteItemAsync(LEGACY_TOKEN_KEY).catch(() => {})
}

/** v1 两键 → v2 单键。两键读齐才算有配置；迁移写入失败时仍返回读到的配置（下次加载再迁），旧键不删 */
async function migrateLegacy(): Promise<ServerConfig | null> {
  const raw = await AsyncStorage.getItem(LEGACY_CONFIG_KEY)
  if (!raw) return null
  const base = JSON.parse(raw) as Omit<ServerConfig, 'token'>
  if (!base || typeof base.edgeUrl !== 'string' || !base.edgeUrl) return null
  const token = (await SecureStore.getItemAsync(LEGACY_TOKEN_KEY)) ?? ''
  if (!token) return null
  const cfg: ServerConfig = { ...base, token }
  try {
    await writeConfig(cfg)
  } catch {
    return cfg
  }
  await dropLegacy()
  return cfg
}

export async function loadServerConfig(): Promise<ServerConfig | null> {
  try {
    const current = parseConfig(await SecureStore.getItemAsync(CONFIG_KEY))
    if (current) return current
    return await migrateLegacy()
  } catch {
    return null
  }
}

export async function saveServerConfig(cfg: ServerConfig): Promise<void> {
  await writeConfig(cfg)
  // 新键已是唯一真相；旧键留着只会在新键被清掉后「复活」一份过期配置
  await dropLegacy()
  notify()
}

export async function clearServerConfig(): Promise<void> {
  await SecureStore.deleteItemAsync(CONFIG_KEY)
  await dropLegacy()
  notify()
}
