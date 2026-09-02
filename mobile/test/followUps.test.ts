// follow-up chips（方案 §5.2）：follow_up 排第一、去重、上限 4；候选集 chip 的文本 sendRouter 必须认得。
import { emptyCandidates, type CandidateState } from '@/core/session/candidates'
import { MAX_CHIPS, followUpChips } from '@/core/session/followUps'
import { routeSend } from '@/core/session/sendRouter'

const ctx = (candidates: CandidateState) => ({ candidates, locationEnabled: true })

test('follow_up 排第一；空候选 + 无 follow_up → 空数组（chips 行不渲染）', () => {
  expect(followUpChips('要不要看明天的？', emptyCandidates())).toEqual([{ label: '要不要看明天的？', text: '要不要看明天的？' }])
  expect(followUpChips(undefined, emptyCandidates())).toEqual([])
  expect(followUpChips('  ', emptyCandidates())).toEqual([])
})

test('候选集 chips 的文本 sendRouter 都认得（chip 是合成一句话，不是新通道）', () => {
  const cand: CandidateState = {
    ...emptyCandidates(),
    category: { keyword: '咖啡', page: 1 },
    poiNames: ['星巴克', '瑞幸'],
    placeItems: [
      { id: 'B1', name: '星巴克' },
      { id: 'B2', name: '瑞幸' },
    ],
  }
  const chips = followUpChips(undefined, cand)
  const refresh = routeSend(chips.find((c) => c.label === '换一批')!.text, ctx(cand))
  expect(refresh.kind).toBe('dispatch')
  expect(refresh.kind === 'dispatch' && refresh.categoryPage).toBe(2)
  const nav = routeSend(chips.find((c) => c.label === '导航去第一个')!.text, ctx(cand))
  expect(nav.kind === 'dispatch' && nav.text).toBe('导航去星巴克')
})

test('poi_list（无 placeItems）的「第一个」→ 导航去{名称}', () => {
  const cand: CandidateState = { ...emptyCandidates(), poiNames: ['加油站A', '加油站B'] }
  const chips = followUpChips(undefined, cand)
  const nav = routeSend(chips.find((c) => c.label === '导航去第一个')!.text, ctx(cand))
  expect(nav.kind === 'dispatch' && nav.text).toBe('导航去加油站A')
})

test('intent_choice 选项直接成 chip（label→send_text）；去重；上限 MAX_CHIPS', () => {
  const cand: CandidateState = {
    ...emptyCandidates(),
    intentChoice: { options: [{ label: '查天气', send_text: '查深圳天气' }, { label: '查空气', send_text: '查深圳空气质量' }, { label: '查天气', send_text: '查深圳天气' }] },
    category: { keyword: '咖啡', page: 1 },
    poiNames: ['a', 'b'],
  }
  const chips = followUpChips('要不要看明天的？', cand)
  expect(chips).toHaveLength(MAX_CHIPS)
  expect(new Set(chips.map((c) => c.text)).size).toBe(MAX_CHIPS)
  expect(chips[0].text).toBe('要不要看明天的？')
})

// ⚠ 上面那条用例的「去重」是**够不到的**：它的夹具里重复项排在第 5 位，MAX_CHIPS 先把它挡了
//    （反向验证实测：把 push 的去重整句删掉，四条用例一条不红）。这条把重复放在上限之内。
test('去重：候选 chip 与另一个候选撞同一句时只留一条（上限之内，不靠 MAX_CHIPS 挡）', () => {
  const cand: CandidateState = {
    ...emptyCandidates(),
    category: { keyword: '咖啡', page: 1 },
    intentChoice: { options: [{ label: '再来一组', send_text: '换一批' }, { label: '查空气', send_text: '查深圳空气质量' }] },
  }
  const chips = followUpChips(undefined, cand)
  expect(chips.map((c) => c.text)).toEqual(['换一批', '查深圳空气质量']) // 只 2 条，离 MAX_CHIPS 还远
})

// B4-11 §6「chips ≤3」：行车档给 max=3。上一条用例（MAX_CHIPS）证明不了这件事——
// 它量的是默认值那一档；这条量的是**传进来的那个 max 真的在管事**。
test('max 参数：行车档 3 条封顶，且留下的是排在前面的三条（顺序判据不变）', () => {
  const cand: CandidateState = {
    ...emptyCandidates(),
    intentChoice: { options: [{ label: '查天气', send_text: '查深圳天气' }, { label: '查空气', send_text: '查深圳空气质量' }] },
    category: { keyword: '咖啡', page: 1 },
    poiNames: ['a', 'b'],
  }
  const parked = followUpChips('要不要看明天的？', cand)
  const driving = followUpChips('要不要看明天的？', cand, 3)
  expect(parked).toHaveLength(MAX_CHIPS)
  expect(driving).toHaveLength(3)
  expect(driving.map((c) => c.text)).toEqual(parked.slice(0, 3).map((c) => c.text))
})
