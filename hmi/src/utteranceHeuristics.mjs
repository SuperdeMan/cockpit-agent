// R4.3b P1 语音定稿启发式——纯逻辑、零依赖、node 可测。供 voiceLoop.mjs 编排调用。
// 四个判定：完整度（端点宽限合并门控）/ 语气词 / 退出词 / 唤醒词残留剥离（P2 用先落着）。
// 都做成纯函数：换车机 DSP 不影响这些语言学判据，且每条都有 utteranceHeuristics.test.mjs 覆盖。

// 汉字按码点计数（'嗯'=1、'好的'=2），避免 UTF-16 代理对误判长度。
export function graphemeLen(s) {
  return [...(s || '')].length
}

// 结尾语气词/标点（去尾用）：退出词前缀匹配、完整度判定都要先剥它们。
const TAIL_PARTICLES = /[吧呀啦了哦嘛呗，。！？,.!?…\s]+$/u

// 语气词/口头噪声字符集（去标点空格后整句只由这些字组成 → 不是请求，静默丢弃 U5a）。
// P4：放宽到 6 个并先去标点空格——ASR 常带尾标点（「嗯，」）或叠字（「嗯嗯嗯嗯嗯」），精确 ^…$ 太脆。
const FILLER_CHARS = '嗯啊哦呃额呀哈嘿哟欸诶唔呐呵'
const FILLER_RE = new RegExp(`^[${FILLER_CHARS}]{1,6}$`, 'u')
const PUNCT_WS = /[，。、！？,.!?…·\s]+/gu

// 完整句信号：疑问/完结标点或语气尾、车控祈使短句、数字+单位——命中即直发不进宽限。
const COMPLETE_TAIL_RE = /(吗|呢|吧|呗|啊|哈|了吗|好了|可以了|谢谢|结束)$/u
const COMMAND_RE = /(打开|关闭|开启|关掉|关闭|播放|暂停|继续|切歌|下一首|上一首|调到|调高|调低|设为|设置|导航到|打给|拨打|发送)[^，。！？,.!?]{0,10}$/u
const NUMBER_UNIT_RE = /\d+\s*(度|档|公里|千米|米|分钟|小时|块|元|个|首|遍|层|楼|号)$/u

// 悬挂结尾：介词/连词/助词/未完动词收尾——大概率没说完，进宽限等续说（U5b）。
// 刻意都是"语义上要求后续宾语/补语"的词；不含会误伤完整短句的字。
const HANGING_RE = /(到|去|和|跟|还有|然后|接着|以及|帮我|我要|我想|请|想|要|把|给|查一下|查查|搜一下|看看|的|是|在|去了吗|从|往|向)$/u

/** 完整度启发式：true=可直发（不拖慢快指令）；false=疑似没说完，进端点宽限等续说。
 *  保守偏 true——宁可偶尔不合并（多一次交互），不无谓地给每句加 700ms 延迟。 */
export function looksComplete(text) {
  const t = (text || '').trim()
  if (!t) return true
  if (/[?？。！!]$/u.test(t)) return true          // 显式句末标点
  if (COMPLETE_TAIL_RE.test(t)) return true          // 疑问/完结语气尾
  if (NUMBER_UNIT_RE.test(t)) return true            // 数字+单位（打开空调 26 度）
  if (COMMAND_RE.test(t)) return true                // 车控/通讯祈使短句
  if (HANGING_RE.test(t)) return false               // 悬挂结尾 → 进宽限
  return true                                        // 其余默认直发
}

/** 纯语气词/口头噪声（嗯嗯/啊/哈哈/唔/「嗯，」）→ true，静默丢弃不上云。去标点空格后判。 */
export function isFiller(text) {
  const t = (text || '').trim().replace(PUNCT_WS, '')
  return !!t && FILLER_RE.test(t)
}

// 退出词占据整句的宽容度：允许识别文本比退出词多至多这么多字（容忍尾部语气/1 个同音错字）。
const EXIT_SLACK = 1

/** 退出/dismiss 词匹配：去尾语气词后判「占据整句」——等于退出词，或以退出词开头且总长 ≤ 词长+slack。
 *  P4 真麦修复：纯精确匹配对 ASR 噪声太脆（同音错字「退下把」、pre-roll 前缀污染都会失配）；
 *  「占据整句+小 slack」既容忍这些噪声，又不吞带宾语的真命令——「退出导航」4 字 > 「退出」2+1，不命中。 */
export function matchExitWord(text, words) {
  const raw = (text || '').trim()
  if (!raw) return false
  const stripped = raw.replace(TAIL_PARTICLES, '').replace(/^[，,。、：:\s]+/u, '')
  const slen = [...stripped].length
  return (words || []).some((w) => {
    if (!w) return false
    if (raw === w || stripped === w) return true
    return stripped.startsWith(w) && slen <= [...w].length + EXIT_SLACK
  })
}

/** 剥离定稿开头的唤醒词残留（P2 pre-roll 可能带「…小舟」尾巴）：命中最长的前缀词并去掉，
 *  再清开头的标点/空白。words 传唤醒词整词 + 单字残片（如 ['小舟小舟','小舟','舟']）。 */
export function stripLeadingWakeWord(text, words) {
  let t = (text || '').trim()
  // 长词优先，避免「小舟小舟」被「小舟」只剥一半
  const sorted = [...(words || [])].filter(Boolean).sort((a, b) => b.length - a.length)
  for (const w of sorted) {
    if (t.startsWith(w)) { t = t.slice(w.length); break }
  }
  return t.replace(/^[，,、。：:\s]+/u, '').trim()
}
