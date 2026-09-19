// 服务器配置持久化的原子性（2026-09-19 GPT-6 评审 F03）。
//
// 坏法：token 与其余字段分两处写、分两处读，没有共同版本 ⇒ 第二笔写入失败 / 进程中途被杀 = 「旧地址 + 新 token」，
// 新凭据被发到旧地址。修法是整份配置一个键；这里验的不是「两笔都成功的路径」，而是**每个写入点注入失败**之后
// 读回来的到底是什么：只允许「完整旧配置 / 完整新配置 / 未配置」三种状态。
//
//  ① 切换配置时写入失败：读回完整旧配置，不是混合；订阅者不被通知
//  ② 写入成功：读回完整新配置；订阅者被通知一次
//  ③ v1 两键 → v2 单键迁移：读齐才迁，迁后旧键清掉、新键独存
//  ④ v1 缺一键：按「未配置」（fail-closed），不写新键
//  ⑤ 迁移写入失败：仍返回读到的配置、旧键保留，下次加载再迁成功
//  ⑥ 超过安全存储上限：显式失败、不写、不通知（截断的 token 是另一种混合配置）
//  ⑦ 清除：新旧键都清
//  ⑧ 并发保存：终态是其中一份完整配置，绝不混合
import AsyncStorage from '@react-native-async-storage/async-storage'

import type { ServerConfig } from '@/core/config/types'

const mockSecure = {
  items: new Map<string, string>(),
  failNextSet: null as Error | null,
  sets: [] as string[],
}
jest.mock('expo-secure-store', () => ({
  getItemAsync: async (k: string) => mockSecure.items.get(k) ?? null,
  setItemAsync: async (k: string, v: string) => {
    mockSecure.sets.push(k)
    if (mockSecure.failNextSet) {
      const e = mockSecure.failNextSet
      mockSecure.failNextSet = null
      throw e
    }
    mockSecure.items.set(k, v)
  },
  deleteItemAsync: async (k: string) => { mockSecure.items.delete(k) },
}))

import {
  SECURE_VALUE_LIMIT,
  clearServerConfig,
  loadServerConfig,
  saveServerConfig,
  subscribeServerConfig,
} from '@/core/config/storage'

const V2 = 'xiaozhou.server-config.v2'
const V1_CONFIG = 'xiaozhou.server-config.v1'
const V1_TOKEN = 'xiaozhou.server-token.v1'

const A: ServerConfig = { preset: 'cloud', fqdn: 'a.tail.ts.net', edgeUrl: 'https://a.tail.ts.net:8443', audioUrl: 'https://a.tail.ts.net:8444', token: 'token-A' }
const B: ServerConfig = { preset: 'custom', edgeUrl: 'https://b.example:8443', audioUrl: 'https://b.example:8444', token: 'token-B' }

// 官方内存 mock 的缺省实现；① 注入的 once-失败在单键实现里不会被消费，得在每条用例前清掉再装回去
const realSetItem = (AsyncStorage.setItem as jest.Mock).getMockImplementation()!
beforeEach(async () => {
  mockSecure.items.clear()
  mockSecure.failNextSet = null
  mockSecure.sets.length = 0
  ;(AsyncStorage.setItem as jest.Mock).mockReset().mockImplementation(realSetItem)
  await AsyncStorage.clear()
})

/** 不预设「哪一笔会失败」：安全存储与 AsyncStorage 各注一次失败，终态都只许是完整 A 或完整 B。
 *  两键实现里只有「第二笔失败」才露馅（token 先落地 ⇒ 旧地址 + 新 token），单注第一笔是在替它遮丑。 */
test.each([
  ['安全存储写入失败', () => { mockSecure.failNextSet = new Error('keystore write failed') }],
  ['AsyncStorage 写入失败', () => { (AsyncStorage.setItem as jest.Mock).mockRejectedValueOnce(new Error('disk full')) }],
])('① 切换配置时%s：读回的只能是完整 A 或完整 B，绝不「旧地址 + 新 token」', async (_name, inject) => {
  await saveServerConfig(A)
  const seen = jest.fn()
  const off = subscribeServerConfig(seen)
  inject()
  let failed = false
  try { await saveServerConfig(B) } catch { failed = true }
  off()
  const loaded = await loadServerConfig()
  expect([A, B]).toContainEqual(loaded)
  // 失败了就不许通知；成功了才通知一次
  expect(seen).toHaveBeenCalledTimes(failed ? 0 : 1)
  if (failed) expect(loaded).toEqual(A)
})

test('② 写入成功：读回完整新配置；订阅者被通知一次', async () => {
  await saveServerConfig(A)
  const seen = jest.fn()
  const off = subscribeServerConfig(seen)
  await saveServerConfig(B)
  off()
  expect(await loadServerConfig()).toEqual(B)
  expect(seen).toHaveBeenCalledTimes(1)
})

test('③ v1 两键在首次加载时迁成 v2 单键：迁后旧键清掉、新键独存，再加载走新键', async () => {
  const { token, ...base } = A
  await AsyncStorage.setItem(V1_CONFIG, JSON.stringify(base))
  mockSecure.items.set(V1_TOKEN, token)
  expect(await loadServerConfig()).toEqual(A)
  expect(mockSecure.items.has(V2)).toBe(true)
  expect(mockSecure.items.has(V1_TOKEN)).toBe(false)
  expect(await AsyncStorage.getItem(V1_CONFIG)).toBeNull()
  mockSecure.sets.length = 0
  expect(await loadServerConfig()).toEqual(A)
  expect(mockSecure.sets).toEqual([]) // 第二次加载不再写
})

test('④ v1 缺一键：按「未配置」，不写新键', async () => {
  const { token, ...base } = A
  await AsyncStorage.setItem(V1_CONFIG, JSON.stringify(base))
  expect(await loadServerConfig()).toBeNull()
  expect(mockSecure.items.has(V2)).toBe(false)

  await AsyncStorage.clear()
  mockSecure.items.set(V1_TOKEN, token)
  expect(await loadServerConfig()).toBeNull()
  expect(mockSecure.items.has(V2)).toBe(false)
})

test('⑤ 迁移写入失败：仍返回读到的配置、旧键保留，下次加载再迁成功', async () => {
  const { token, ...base } = A
  await AsyncStorage.setItem(V1_CONFIG, JSON.stringify(base))
  mockSecure.items.set(V1_TOKEN, token)
  mockSecure.failNextSet = new Error('keystore busy')
  expect(await loadServerConfig()).toEqual(A)
  expect(mockSecure.items.has(V2)).toBe(false)
  expect(mockSecure.items.get(V1_TOKEN)).toBe(token)
  expect(await AsyncStorage.getItem(V1_CONFIG)).not.toBeNull()

  expect(await loadServerConfig()).toEqual(A)
  expect(mockSecure.items.has(V2)).toBe(true)
  expect(mockSecure.items.has(V1_TOKEN)).toBe(false)
})

test('⑥ 超过安全存储上限：显式失败、不写、不通知', async () => {
  await saveServerConfig(A)
  const seen = jest.fn()
  const off = subscribeServerConfig(seen)
  const huge: ServerConfig = { ...B, token: 'x'.repeat(SECURE_VALUE_LIMIT) }
  await expect(saveServerConfig(huge)).rejects.toThrow('上限')
  off()
  expect(await loadServerConfig()).toEqual(A)
  expect(seen).not.toHaveBeenCalled()
})

test('⑦ 清除：新旧键都清，之后是未配置', async () => {
  await saveServerConfig(A)
  const { token, ...base } = B
  await AsyncStorage.setItem(V1_CONFIG, JSON.stringify(base))
  mockSecure.items.set(V1_TOKEN, token)
  await clearServerConfig()
  expect(await loadServerConfig()).toBeNull()
  expect(mockSecure.items.size).toBe(0)
  expect(await AsyncStorage.getItem(V1_CONFIG)).toBeNull()
})

test('⑧ 并发保存：终态是其中一份完整配置，绝不混合', async () => {
  await Promise.all([saveServerConfig(A), saveServerConfig(B)])
  const loaded = await loadServerConfig()
  expect([A, B]).toContainEqual(loaded)
})
