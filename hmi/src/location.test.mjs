import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildLocationMeta,
  buildRequestLocationMeta,
  shouldRequestLocationConsent,
  isLocationDependent,
} from './location.mjs'

test('isLocationDependent flags navigation / nearest / where-am-i / unscoped weather', () => {
  assert.equal(isLocationDependent('导航去最近的粤菜馆'), true)
  assert.equal(isLocationDependent('附近的充电站'), true)
  assert.equal(isLocationDependent('我现在在哪里'), true)
  assert.equal(isLocationDependent('今天天气怎么样'), true)
  // 已含明确城市的天气不需要当前定位；纯闲聊也不需要
  assert.equal(isLocationDependent('深圳天气怎么样'), false)
  assert.equal(isLocationDependent('讲个笑话'), false)
})

test('serializes one browser-approved position into request-only location meta', () => {
  assert.deepEqual(buildLocationMeta({ lat: 39.92, lng: 116.41, accuracyM: 12, capturedAt: 123 }), {
    current_lat: '39.920000',
    current_lng: '116.410000',
    current_accuracy_m: '12',
    current_location_at: '123',
    current_location_source: 'browser',
  })
})

test('does not send invalid coordinates', () => {
  assert.deepEqual(buildLocationMeta({ lat: 91, lng: 116.41 }), {})
})

test('does not attach a previously captured location after the setting is disabled', () => {
  const location = { lat: 39.92, lng: 116.41, accuracyM: 12, capturedAt: 1_781_700_000_000 }
  assert.deepEqual(buildRequestLocationMeta(false, location), {})
  assert.equal(buildRequestLocationMeta(true, location).current_lat, '39.920000')
})

test('asks for consent for weather without a named place and for navigation origin', () => {
  assert.equal(shouldRequestLocationConsent('今天天气怎么样', false), true)
  assert.equal(shouldRequestLocationConsent('我这里天气怎么样', false), true)
  assert.equal(shouldRequestLocationConsent('导航去东方明珠', false), true)
  assert.equal(shouldRequestLocationConsent('深圳天气怎么样', false), false)
  assert.equal(shouldRequestLocationConsent('今天天气怎么样', true), false)
})

test('isLocationDependent flags charging / trip-planning queries', () => {
  assert.equal(isLocationDependent('是否需要中途充电'), true)
  assert.equal(isLocationDependent('周末去杭州两天，带老人，顺便看看是否需要中途充电'), true)
  assert.equal(isLocationDependent('帮我规划行程'), true)
})

// ─── QA 卡 Q4：位置前置闸收窄（I-007 / I-032①）────────────────────────
// 四个各自独立的缺陷叠在一起：子串匹配没有意图概念 / EXPLICIT_PLACE 只对天气生效 /
// 没有否定排除 / 闸拦的是整句。判据：**客户端不该做意图判定**——这道闸真正要问的是
// 「本轮执行是否需要当前坐标」，那个答案只有编排侧知道，所以闸只保留最保守的一档。

test('Q4①: 车控对象不是位置查询（词表收窄，不加第二份白名单）', () => {
  assert.equal(isLocationDependent('打开充电口'), false)
  assert.equal(isLocationDependent('把充电口盖关上'), false)
  assert.equal(isLocationDependent('续航还剩多少'), false)   // 车况问题，不是位置问题
  // 对照：真正的找电桩仍然要定位
  assert.equal(isLocationDependent('找个充电站'), true)
  assert.equal(isLocationDependent('附近有充电桩吗'), true)
})

test('Q4②: 显式地点线索对「就近类」同样生效，不只对天气', () => {
  assert.equal(isLocationDependent('查深圳欢乐海岸周边停车场'), false)
  assert.equal(isLocationDependent('上海南京路附近有什么好吃的'), false)
  // 对照：没有地点线索的就近查询仍然要定位
  assert.equal(isLocationDependent('附近有什么好吃的'), true)
  assert.equal(isLocationDependent('最近的停车场在哪'), true)
})

test('Q4②b: 导航/行程要的是**出发地**，显式目的地不能豁免', () => {
  assert.equal(isLocationDependent('导航去东方明珠'), true)
  assert.equal(isLocationDependent('导航去深圳湾公园'), true)   // 深圳湾公园命中 EXPLICIT_PLACE 也不豁免
  assert.equal(isLocationDependent('自驾去杭州两天'), true)
  // ⚠ 留痕：首版这里写的是「周末去杭州两天 → true」，实测 false——**收窄前也是 false**
  // （旧词表同样不含裸「去」）。那是我凭空加的一条**扩大**闸的要求，与 Q4 的方向相反。
  // 尺子写错就改尺子（§4.3），但要留下改过的痕迹。
  assert.equal(isLocationDependent('周末去杭州两天'), false)
})

test('Q4③: 否定/取消句不是位置请求', () => {
  assert.equal(isLocationDependent('取消当前导航'), false)
  assert.equal(isLocationDependent('别开始导航'), false)
  assert.equal(isLocationDependent('不用导航了'), false)
  assert.equal(isLocationDependent('停止导航'), false)
  // 对照：否定词离位置词远（不是在否定它）时不误伤
  assert.equal(isLocationDependent('别忘了提醒我到公司充电站补电'), true)
})

test('Q4④: 多意图句不因为一个位置词整句被拦', () => {
  assert.equal(shouldRequestLocationConsent('关空调，查深圳天气，再看看股票', false), false)
  assert.equal(shouldRequestLocationConsent('取消当前导航，顺便把空调关了', false), false)
})
