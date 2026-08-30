// mobile/test/presenceFixtures.test.ts
// 状态画廊的覆盖度守卫（同 card-gallery 的「注册表卡型必须都有样本」）：
// 每个 primary（8 个光球态）与每种 degradation（7 种）都要有样本，缺一即红——
// 「少了谁」得当场看得见，不能等到事后数截图。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { pinCommitment, type DockItem } from '@/core/presence/commitment'
import { presenceFixtures } from '@/core/presence/fixtures'
import type { Degradation, MicState, OrbState } from '@/core/presence/presence'

const PRIMARIES: OrbState[] = ['idle', 'armed', 'listening', 'thinking', 'speaking', 'attention', 'looking', 'muted']
const DEGRADATIONS: Degradation['kind'][] = [
  'recoverable_error', 'transport_unknown', 'permission_denied', 'service_degraded', 'safety_blocked', 'audio_echo_degraded', 'fatal',
]

test('每个 primary 至少一条样本', () => {
  const covered = new Set(presenceFixtures().map((f) => f.snapshot.primary))
  expect(PRIMARIES.filter((s) => !covered.has(s))).toEqual([])
})

test('每种 degradation 至少一条样本', () => {
  const covered = new Set(presenceFixtures().flatMap((f) => f.snapshot.degradation.map((d) => d.kind)))
  expect(DEGRADATIONS.filter((k) => !covered.has(k))).toEqual([])
})

test('privacy.mic 四档各有样本（B2 T2 加档：画廊要能看见每一档的文案与颜色）', () => {
  const covered = new Set(presenceFixtures().map((f) => f.snapshot.privacy.mic))
  const MICS: MicState[] = ['off', 'edge', 'cloudAsr', 'cloudAudio']
  expect(MICS.filter((m) => !covered.has(m))).toEqual([])
})

test('armed 有「胶囊在窗内」与「3s 后无胶囊」两条样本（评审 D2 的画廊证据）', () => {
  const armed = presenceFixtures().filter((f) => f.snapshot.primary === 'armed')
  expect(armed.some((f) => f.snapshot.capsule?.text === '说「小舟小舟」')).toBe(true)
  expect(armed.some((f) => f.snapshot.capsule === undefined)).toBe(true)
})

test('样本标签唯一（?only= 直达靠它）', () => {
  const labels = presenceFixtures().map((f) => f.label)
  expect(new Set(labels).size).toBe(labels.length)
})

// 第四条守卫（第 2 批实测补）：上面两条只守 primary 与 degradation，**不守 Dock 分支**——
// 而 Maestro 09 的断言恰恰落在 Dock 分支的文案上。实测：queue 分支曾经 0 条样本渲得出来
// （两条带 queued 的样本都同时带 pendingOps，confirm 排 rank 0、queue 排 rank 4，
//  queue 永远只进「另有 N 个待处理」的计数），于是计划里那条 `assertVisible: "条消息排队中"`
// 是一条**永远红**的断言，而没有任何单测能告诉你这件事。
// ⚠ `slot` 刻意不在这条守卫里：`derivePresence` 没有产出它的代码路径（协议无 missing_slots），
// 在「样本走 derivePresence 本尊」这条纪律下画廊**无从证明**它渲得出来——把它写进守卫
// 只会逼人手搓一个假 snapshot 来喂绿灯。
test('可产出的三种 DockItem 各有一条样本会被 pin 住（只有被钉住的那项才渲分支文案）', () => {
  const pinned = new Set(
    presenceFixtures().flatMap((f) => {
      const top = pinCommitment(f.snapshot.commitment)
      return top ? [top.item.kind] : []
    }),
  )
  const PINNABLE: DockItem['kind'][] = ['confirm', 'task', 'queue']
  expect(PINNABLE.filter((k) => !pinned.has(k))).toEqual([])
})

// 第五条守卫（第 2 批实测补）：「真栈可产」那个 chip 是**取证屏上的一句断言**，而手写的断言会漂。
// 实测：`deg-recoverable` 与 `deg-fatal` 两条都标着「真栈可产」，可 `usePresence` 从来不产它们
// ——截图上那个 chip 就是一句假话，而它恰恰是这块屏用来分「读数」与「样本」的唯一标记
// （同 card-gallery 头注那条「样本截图不是读数」）。
// ⇒ 判据从**产出方源码**盘点，不从这份文件的自我声明里取（同族做法：产出方静态盘点）。
test('降级样本的「真栈可产」标记必须与 usePresence 的实际产出方一致', () => {
  const src = readFileSync(resolve(__dirname, '../src/features/chat/usePresence.ts'), 'utf8')
  const produced = new Set([...src.matchAll(/degradations\.push\(\{\s*kind:\s*'([a-z_]+)'/g)].map((m) => m[1]))
  // 观测通道自检：正则一旦被改坏，`produced` 会是空集，而空集会把**每一条**样本都判成说谎
  // ——那时红灯指向的是这条测试自己，不是样本。先证明通道开着再读结论。
  expect(produced.size).toBeGreaterThan(0)
  const lying = presenceFixtures()
    .filter((f) => f.producible && f.snapshot.degradation.some((d) => !produced.has(d.kind)))
    .map((f) => f.label)
  expect(lying).toEqual([])
})
