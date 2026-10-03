// 车控意图 → 人话（v3 P4c，D17）与声明源对账：**跨进程消费用测试对账声明源**。
// 中文对象名的声明源是 orchestrator/edge/knowledge/commands.yaml（每个对象的 display_name + edge_intents）；
// 手机端的 OBJECT_NAME 是副本。不引 YAML 库（mobile 没有直接依赖），按行读 `objects:` 下的两个字段——
// 先证声明源读到了（对象数 / 意图数下限），再两个方向逐条比：每个端侧意图都落到它所属对象的中文名；
// 表里每个键都还在声明源里（对象删了不会在这里留一个死名字）。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import {
  ACTION_WORD, FEATURE_NAME, OBJECT_NAME, controlActionWord, controlFeatureName, controlObjectName,
} from '@/core/cards/controlNames'

type Decl = { key: string; displayName: string; intents: string[] }

function declaredObjects(): Decl[] {
  const src = readFileSync(resolve(__dirname, '../../orchestrator/edge/knowledge/commands.yaml'), 'utf8')
  const out: Decl[] = []
  let cur: Decl | null = null
  let inIntents = false
  for (const line of src.split(/\r?\n/)) {
    const obj = /^ {2}([a-z_]+):\s*$/.exec(line)
    if (obj) {
      cur = { key: obj[1], displayName: '', intents: [] }
      out.push(cur)
      inIntents = false
      continue
    }
    if (!cur) continue
    const name = /^ {4}display_name:\s*['"]?(.+?)['"]?\s*$/.exec(line)
    if (name) {
      cur.displayName = name[1]
      continue
    }
    if (/^ {4}edge_intents:\s*$/.test(line)) {
      inIntents = true
      continue
    }
    if (inIntents) {
      const item = /^ {4}- ([a-z_.]+)\s*$/.exec(line)
      if (item) cur.intents.push(item[1])
      else inIntents = false
    }
  }
  return out
}

const objects = declaredObjects()
const intents = objects.flatMap((o) => o.intents.map((intent) => ({ intent, object: o })))

test('声明源读到了：对象带中文名，端侧意图不少于 80 条', () => {
  expect(objects.length).toBeGreaterThan(30)
  expect(objects.every((o) => o.displayName)).toBe(true)
  expect(intents.length).toBeGreaterThanOrEqual(80)
})

test('每个端侧意图都按首段落到它所属对象的 display_name（hvac → 空调、tire_pressure → 胎压监测 也在内）', () => {
  const wrong = intents
    .filter(({ intent, object }) => controlObjectName(intent) !== object.displayName)
    .map(({ intent, object }) => `${intent}: ${controlObjectName(intent) || '（空）'} ≠ ${object.displayName}`)
  expect(wrong).toEqual([])
})

test('表里每个键都还在声明源里：是对象键（中文名一致），或是某个端侧意图的首段', () => {
  const stale = Object.entries(OBJECT_NAME).filter(([key, name]) => {
    const asObject = objects.find((o) => o.key === key)
    if (asObject) return asObject.displayName !== name
    return !intents.some(({ intent, object }) => intent.split('.')[0] === key && object.displayName === name)
  })
  expect(stale).toEqual([])
})

test('每个三段式端侧意图都有子功能名，每个端侧意图的动作都有动作词', () => {
  const noFeature = intents.map((x) => x.intent).filter((i) => i.split('.').length >= 3 && !controlFeatureName(i))
  const noAction = intents.map((x) => x.intent).filter((i) => !controlActionWord(i))
  expect(noFeature).toEqual([])
  expect(noAction).toEqual([])
  expect(Object.keys(FEATURE_NAME).length).toBeGreaterThan(0)
  expect(Object.keys(ACTION_WORD).length).toBeGreaterThan(0)
})

test('认不出的意图名给空串（调用方回落「车辆操作」，不显示机器名）', () => {
  expect(controlObjectName('teleport.on')).toBe('')
  expect(controlObjectName('')).toBe('')
  expect(controlFeatureName('hvac.set')).toBe('')
  expect(controlActionWord('hvac.levitate')).toBe('')
  expect(controlObjectName('seat.heating.on')).toBe('座椅')
  expect(controlFeatureName('seat.heating.on')).toBe('加热')
  expect(controlActionWord('seat.heating.on')).toBe('打开')
})
