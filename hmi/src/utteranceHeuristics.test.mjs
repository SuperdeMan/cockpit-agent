import test from 'node:test'
import assert from 'node:assert/strict'

import { looksComplete, isFiller, matchExitWord, stripLeadingWakeWord, graphemeLen } from './utteranceHeuristics.mjs'

// ─── 完整度启发式（端点宽限合并门控）───

test('looksComplete：车控/数字单位短句 → 直发（不进宽限，不拖慢快指令）', () => {
  assert.equal(looksComplete('打开空调26度'), true)
  assert.equal(looksComplete('打开空调 26 度'), true)
  assert.equal(looksComplete('打开主驾座椅加热'), true)
  assert.equal(looksComplete('播放周杰伦的歌'), true)
  assert.equal(looksComplete('音量调到20'), true)
})

test('looksComplete：疑问/完结结尾 → 直发', () => {
  assert.equal(looksComplete('今天天气怎么样？'), true)
  assert.equal(looksComplete('可以了吗'), true)
  assert.equal(looksComplete('好了'), true)
  assert.equal(looksComplete('导航去西溪湿地'), true) // 完整地名收尾，默认直发
})

test('looksComplete：悬挂结尾（介词/助动词/未完动词）→ 进宽限', () => {
  assert.equal(looksComplete('导航去'), false)
  assert.equal(looksComplete('帮我'), false)
  assert.equal(looksComplete('查一下'), false)
  assert.equal(looksComplete('我想'), false)
  assert.equal(looksComplete('然后'), false)
})

test('looksComplete：空串保守判完整（不进宽限）', () => {
  assert.equal(looksComplete(''), true)
  assert.equal(looksComplete('   '), true)
})

// ─── 语气词过滤（U5a）───

test('isFiller：纯语气词/口头噪声命中', () => {
  for (const s of ['嗯', '嗯嗯', '啊', '啊啊', '哈哈', '哦', '呃', '唔', '诶']) {
    assert.equal(isFiller(s), true, `应命中 filler: ${s}`)
  }
})

test('isFiller：含实义字不命中（避免误杀请求）', () => {
  for (const s of ['嗯好的', '啊对', '哈喽小舟', '导航', '5 个字以上语气词啊啊啊啊啊']) {
    assert.equal(isFiller(s), false, `不应命中 filler: ${s}`)
  }
})

// ─── 退出/dismiss 词（U3）───

test('matchExitWord：去尾语气词后精确匹配（修「没事了」「退下吧」）', () => {
  const words = ['退下', '退下吧', '再见', '闭嘴', '别说了', '没事', '不用了']
  assert.equal(matchExitWord('退下吧', words), true)
  assert.equal(matchExitWord('退下', words), true)
  assert.equal(matchExitWord('没事了', words), true)   // 去尾「了」→「没事」命中
  assert.equal(matchExitWord('再见啦', words), true)
  assert.equal(matchExitWord('别说了', words), true)   // 整词带「了」也命中（raw 精确）
  assert.equal(matchExitWord('闭嘴', words), true)
})

test('matchExitWord：带宾语的真实命令不误命中（精确匹配的关键收益）', () => {
  const words = ['退下', '退下吧', '退出', '再见', '闭嘴', '别说了', '没事']
  assert.equal(matchExitWord('结束导航', words), false) // 词表刻意不含单独「结束」
  assert.equal(matchExitWord('退出导航', words), false) // 「退出」在词表但「退出导航」是命令，精确匹配不吞
  assert.equal(matchExitWord('没事我自己来', words), false) // 不是纯 dismiss，放行上云
  assert.equal(matchExitWord('导航去西湖', words), false)
  assert.equal(matchExitWord('', words), false)
})

// ─── 唤醒词残留剥离（P2 pre-roll）───

test('stripLeadingWakeWord：剥唤醒词整词与残片 + 清开头标点', () => {
  const words = ['小舟小舟', '小舟', '舟']
  assert.equal(stripLeadingWakeWord('小舟小舟帮我查天气', words), '帮我查天气')
  assert.equal(stripLeadingWakeWord('小舟，导航去公司', words), '导航去公司')
  assert.equal(stripLeadingWakeWord('舟 打开空调', words), '打开空调')
  assert.equal(stripLeadingWakeWord('今天天气怎么样', words), '今天天气怎么样') // 无残留原样返回
})

test('graphemeLen：按码点计数', () => {
  assert.equal(graphemeLen('嗯'), 1)
  assert.equal(graphemeLen('好的'), 2)
  assert.equal(graphemeLen(''), 0)
})
