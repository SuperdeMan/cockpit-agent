// mobile/test/cardFields.test.ts
// 兜底卡 / 行车压缩卡共用的字段探取。charging_list 的形状照产出方源码抄（agent.py:162-171）。
import { cardListRows, cardPrimaryButton, cardPrimaryFields } from '@/core/cards/cardFields'

const chargingList = {
  type: 'charging_list',
  soc: '62%',
  items: [
    { id: 's1', name: '特来电充电站', available: 3, total: 8, price: 1.2, distance_km: 0.8, operator: '特来电' },
    { id: 's2', name: '星星充电', available: 0, total: 4, price: 1.5, distance_km: 2.1, operator: '星星' },
  ],
  buttons: [{ label: '导航去第一个', send_text: '导航去第一个' }],
}

test('charging_list：列表行 = 名字 + 距离 + 空闲；主字段只剩 soc', () => {
  const rows = cardListRows(chargingList)
  expect(rows).toHaveLength(2)
  expect(rows[0]).toEqual({ title: '特来电充电站', sub: '0.8km · 3/8 空闲' })
  expect(rows[1].sub).toBe('2.1km · 0/4 空闲')
  expect(cardPrimaryFields(chargingList)).toEqual([['soc', '62%']])
})

// ⚠ 这条是反向验证补的：计划 §T5 步骤 5 ③ 断言「`total > 0` 改 `>= 0` 恰好红」，实测**不红**——
//   上面的 fixture 两个站 total 都是 8/4，没有任何用例走到 total===0 那一格，守卫是裸的。
test('total===0 的站不写「0/0 空闲」（该守卫的唯一钉子）', () => {
  expect(cardListRows({ items: [{ name: '在建站', total: 0, available: 0, distance_km: 1.5 }] })).toEqual([
    { title: '在建站', sub: '1.5km' },
  ])
})

test('items 最多 5 行；非对象项与无名字项跳过', () => {
  const many = { items: [...Array.from({ length: 8 }, (_, i) => ({ name: `s${i}` })), 'junk', { foo: 1 }] }
  expect(cardListRows(many)).toHaveLength(5)
})

test('无 items 的卡拿不出行；主按钮取第一个可用的', () => {
  expect(cardListRows({ type: 'weather', city: '深圳' })).toEqual([])
  expect(cardPrimaryButton(chargingList)).toEqual({ label: '导航去第一个', send_text: '导航去第一个' })
  expect(cardPrimaryButton({ buttons: [{ label: 'x' }] })).toBeNull()
})
