// C 组：HMI 二次交互 CDP 用例（设计 §5.3）。断言链 = 渲染 → 点击/输入 →
// 「发出的 WS 帧文本/meta 正确」（Network.webSocketFrameSent 实拦）→ 后端续接 → 截图留档。
//
// 用法：node test/hmi_cdp/run_cases.mjs           # 全部
//       node test/hmi_cdp/run_cases.mjs C1 C4    # 指定
// 前置：make up 全栈；宿主 5173 未被本地 vite 占用；真实 key（live 语义类用例）。
import { Cdp, launchBrowser, debugVehicle, vehicleState, sleep } from './driver.mjs'

const results = []
function record(id, ok, detail = '') {
  results.push({ id, ok, detail })
  console.log(`${ok ? '✅' : '❌'} ${id}  ${detail}`)
}

// 等最新一条助手气泡完成（无 pending 光标）且页面包含关键词
async function waitReply(cdp, keyword, timeoutMs = 60000) {
  await cdp.waitFor(
    `document.body.innerText.includes(${JSON.stringify(keyword)})`,
    timeoutMs, `回复含「${keyword}」`)
}

const CASES = {
  // C1 确认条：渲染 → 点「确认」→ 帧带 is_confirmation → 车况真变
  async C1(cdp) {
    await debugVehicle('gear', 'P'); await debugVehicle('speed_kmh', 0)
    const t0 = Date.now()
    await cdp.typeAndSend('打开后备箱')
    await cdp.waitFor(
      `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '确认')`,
      30000, '确认条渲染')
    await cdp.screenshot('C1-confirm-bar')
    await cdp.clickButtonByText('确认')
    const frame = await cdp.waitSentFrame(
      (d) => d.is_confirmation === true, 10000, t0, '确认帧')
    if (frame.text !== '确认') throw new Error(`确认帧文本=${frame.text}`)
    await sleep(4000)
    const st = await vehicleState()
    if (st.trunk !== 'open') throw new Error(`trunk=${st.trunk}`)
    await cdp.typeAndSend('关闭后备箱')            // 复位
    await sleep(2500)
    return '确认条→is_confirmation 帧→trunk=open'
  },

  // C2a place_list 裸序号：「点一下第二个」→ HMI 改写「看{名}的详情」+ meta.nearby_poi_id
  async C2a(cdp) {
    await cdp.typeAndSend('附近有什么好吃的火锅店')
    await cdp.waitFor(
      `document.body.innerText.includes('人均') || document.body.innerText.includes('营业')`,
      60000, 'place_list 渲染')
    await cdp.screenshot('C2a-place-list')
    const t1 = Date.now()
    await cdp.typeAndSend('点一下第二个')
    const frame = await cdp.waitSentFrame(
      (d) => typeof d.text === 'string' && d.text.startsWith('看') && d.text.endsWith('的详情'),
      10000, t1, '详情改写帧')
    if (!frame.meta || !frame.meta.nearby_poi_id) {
      throw new Error(`详情帧缺 meta.nearby_poi_id: ${JSON.stringify(frame.meta || {}).slice(0, 120)}`)
    }
    await waitReply(cdp, '详情', 60000)
    await cdp.screenshot('C2a-place-detail')
    return `改写帧=${frame.text}（poi_id 已透传）`
  },

  // C2b dest_choice：泛目的地充电 → 「第一个」→ HMI 派发候选名本身（回填槽位，非导航改写）。
  // 前提有路由方差（R1 族）：后端可能把「惠州」解析成就近「惠州出口」直接出路线、不出
  // dest_choice 候选——此时「第一个」原样发出（HMI 无候选可改写，非 HMI 缺陷）→ 判 SKIP。
  async C2b(cdp) {
    await debugVehicle('battery', 40)
    await cdp.typeAndSend('去惠州的路上帮我找个充电站')
    await waitReply(cdp, '充电', 90000)
    await cdp.screenshot('C2b-after-query')
    const t1 = Date.now()
    await cdp.typeAndSend('第一个')
    const frame = await cdp.waitSentFrame(
      (d) => typeof d.text === 'string' && d.text.length >= 1 && d.text !== 'start',
      10000, t1, 'dest_choice 后续帧')
    if (frame.text === '第一个') {
      return 'SKIP：后端未出 dest_choice 候选（惠州被就近解析直接规划，R1 族前提未成立）'
    }
    if (/^导航去/.test(frame.text)) throw new Error(`dest_choice 误改写成导航: ${frame.text}`)
    await waitReply(cdp, '充电', 90000)
    await cdp.screenshot('C2b-charging-plan')
    return `回填帧=${frame.text}`
  },

  // C3 scene_list 卡按钮：「有哪些场景」→ 点「露营模式」→ 帧=开启露营模式 → 取消不落动作
  async C3(cdp) {
    await cdp.typeAndSend('有哪些场景')
    await cdp.waitFor(
      `document.body.innerText.includes('露营模式')`, 45000, 'scene_list 渲染')
    await cdp.screenshot('C3-scene-list')
    const t1 = Date.now()
    await cdp.clickButtonByText('露营模式')
    const frame = await cdp.waitSentFrame(
      (d) => typeof d.text === 'string' && d.text.includes('开启') && d.text.includes('露营'),
      10000, t1, '场景激活帧')
    // 露营含座椅放平（危险）→ 确认条；点「取消」验证取消链路且不改车况
    await cdp.waitFor(
      `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '取消')`,
      30000, '确认条（露营含危险动作）')
    await cdp.clickButtonByText('取消')
    await waitReply(cdp, '取消', 20000)
    return `激活帧=${frame.text}；取消链路通`
  },

  // C4 主动推送渲染：分钟级提醒 → 到点卡（琥珀脉冲）→ 点「完成」按钮 → 帧=完成提醒：X
  async C4(cdp) {
    await cdp.typeAndSend('过1分钟提醒我CDP演练')
    await waitReply(cdp, 'CDP演练', 30000)
    await cdp.waitFor(
      `document.body.innerText.includes('提醒到点')`, 150000, '到点推送卡渲染')
    await cdp.screenshot('C4-reminder-fired')
    const t1 = Date.now()
    await cdp.clickButtonByText('完成')
    const frame = await cdp.waitSentFrame(
      (d) => typeof d.text === 'string' && d.text.startsWith('完成提醒'),
      10000, t1, '完成按钮帧')
    await waitReply(cdp, '完成', 20000)
    return `到点卡渲染+按钮帧=${frame.text}`
  },

  // C5 过程区门控：重域任务出四阶段过程区；简单车控不出
  async C5(cdp) {
    await cdp.typeAndSend('把音量调到25')
    await sleep(4000)
    const simple = await cdp.eval(
      `document.body.innerText.includes('理解需求') || document.body.innerText.includes('规划步骤')`)
    if (simple) throw new Error('简单车控出现了过程区')
    await cdp.typeAndSend('帮我深入调研一下车规级芯片的国产化进展')
    await cdp.waitFor(
      `document.body.innerText.includes('理解需求') || document.body.innerText.includes('规划') || document.body.innerText.includes('执行')`,
      60000, '过程区出现')
    await cdp.screenshot('C5-process-region')
    await waitReply(cdp, '调研', 180000)   // 等报告收尾，避免尾流量污染后续用例
    return '重域出过程区 / 简单车控零过程'
  },

  // C6 右舞台联动：车况舞台渲染 debug 压入的真值（HMI 车况动态化，2026-07-13）
  async C6(cdp) {
    await debugVehicle('battery', 55)
    await sleep(3000)
    await cdp.waitFor(
      `document.body.innerText.includes('55')`, 15000, '舞台电量=55')
    await cdp.screenshot('C6-stage-battery')
    return '舞台车况随 debug 压值联动'
  },

  // C7 真实商户卡帧语义：只在显式给出已裁定的完整下单语句时运行，
  // 避免日常 C 组无意创建真实未支付单。用例实拦两类帧：
  //   卡内「查订单」 = is_confirmation === false；
  //   全局「确认下单」 = is_confirmation === true。
  async C7(cdp) {
    const prompt = String(process.env.CDP_MERCHANT_PROMPT || '').trim()
    if (!prompt) return 'SKIP：未设 CDP_MERCHANT_PROMPT（本用例会创建真实未支付订单）'

    await cdp.typeAndSend(prompt)
    await cdp.waitFor(
      `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '确认下单')`,
      90000, '商户订单预览+全局确认条')
    const confirmationText = await cdp.bodyText()
    if (!confirmationText.includes('商户下单')) throw new Error('确认条缺「商户下单」语义')
    if (confirmationText.includes('危险操作')) throw new Error('商户确认条误用车控「危险操作」文案')
    await cdp.screenshot('C7-merchant-preview-confirm')

    const confirmAt = Date.now()
    await cdp.clickButtonByText('确认下单')
    const confirmFrame = await cdp.waitSentFrame(
      (d) => d.is_confirmation === true, 10000, confirmAt, '商户下单确认帧')
    if (confirmFrame.text !== '确认') throw new Error(`确认帧文本=${confirmFrame.text}`)

    await cdp.waitFor(
      `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '查订单')`,
      90000, '订单/支付卡「查订单」动作')
    const orderText = await cdp.bodyText()
    if (orderText.includes('安全支付链接') && orderText.includes('扫码支付')) {
      throw new Error('无二维码的安全支付链接仍标成「扫码」')
    }
    await cdp.screenshot('C7-merchant-order-payment')

    const actionAt = Date.now()
    await cdp.clickButtonByText('查订单')
    const actionFrame = await cdp.waitSentFrame(
      (d) => d.is_confirmation === false && typeof d.text === 'string' && d.text.includes('订单'),
      10000, actionAt, '商户卡普通动作帧')
    const queryReply = await cdp.waitReceivedFrame(
      (data) => data.type === 'final' && typeof data.speech === 'string' &&
        (data.speech.includes('订单') || data.speech.includes('待支付') || data.speech.includes('已取消')),
      60000, actionAt, '商户真实查单回复')
    if (queryReply.speech.includes('联网搜索') || queryReply.speech.includes('检索')) {
      throw new Error(`查订单被通用搜索劫持: ${queryReply.speech.slice(0, 80)}`)
    }
    return `确认帧 is_confirmation=${confirmFrame.is_confirmation}；卡动作帧 is_confirmation=${actionFrame.is_confirmation}；查单已收口`
  },

  // C8 与 C7 必须在同一 Node 进程中连续运行：复用同一 HMI session 和
  // 权威 Task Ledger 归属，真实查询后再由全局确认条提交一次瑞幸取消。
  async C8(cdp) {
    const prompt = String(process.env.CDP_MERCHANT_CANCEL_PROMPT || '').trim()
    if (!prompt) return 'SKIP：未设 CDP_MERCHANT_CANCEL_PROMPT'

    await sleep(5000) // 等 C7 的“查订单”普通动作完整收尾，避免并发轮次串台
    await cdp.typeAndSend(prompt)
    await cdp.waitFor(
      `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '确认取消')`,
      90000, '瑞幸取消预览+全局确认条')
    const pendingText = await cdp.bodyText()
    if (!pendingText.includes('取消订单')) throw new Error('取消确认条缺商户取消语义')
    if (pendingText.includes('危险操作')) throw new Error('取消确认条误用车控文案')
    await cdp.screenshot('C8-luckin-cancel-confirm')

    const confirmAt = Date.now()
    await cdp.clickButtonByText('确认取消')
    const confirmFrame = await cdp.waitSentFrame(
      (d) => d.is_confirmation === true, 10000, confirmAt, '瑞幸取消确认帧')
    if (confirmFrame.text !== '确认') throw new Error(`取消确认帧文本=${confirmFrame.text}`)
    await cdp.waitFor(
      `document.body.innerText.includes('已取消')`, 90000, '瑞幸取消终态')
    await cdp.screenshot('C8-luckin-cancelled')
    return `取消确认帧 is_confirmation=${confirmFrame.is_confirmation}；终态=已取消`
  },

  // C9 只读商户查单：用于已经创建过订单后的浏览器复验。它不点击任何确认按钮，
  // 不允许以“为了重跑 C7”为由再创建一笔真实订单。
  async C9(cdp) {
    const prompt = String(process.env.CDP_MERCHANT_QUERY_PROMPT || '').trim()
    if (!prompt) return 'SKIP：未设 CDP_MERCHANT_QUERY_PROMPT（本用例只读）'
    const expectedStatus = String(process.env.CDP_MERCHANT_EXPECTED_STATUS || '').trim()
    if (!expectedStatus) {
      throw new Error('商户只读查单必须设置 CDP_MERCHANT_EXPECTED_STATUS，不能只凭任意回复判绿')
    }

    const queryAt = Date.now()
    await cdp.typeAndSend(prompt)
    const sentFrame = await cdp.waitSentFrame(
      (data) => data.is_confirmation === false && data.text === prompt,
      10000, queryAt, '商户只读查单帧')
    const reply = await cdp.waitReceivedFrame(
      (data) => data.type === 'final' && typeof data.speech === 'string' &&
        data.speech.includes(expectedStatus),
      90000, queryAt, '商户只读查单回复')
    if (reply.speech.includes('联网搜索') || reply.speech.includes('检索')) {
      throw new Error(`查询结果被通用搜索劫持: ${reply.speech.slice(0, 80)}`)
    }
    const merchantSlug = prompt.includes('麦当劳') ? 'mcd'
      : prompt.includes('瑞幸') ? 'luckin' : 'merchant'
    await cdp.screenshot(`C9-${merchantSlug}-order-query-strict`)
    return `只读查单帧 is_confirmation=${sentFrame.is_confirmation}；商户终态=${expectedStatus}`
  },
}

async function main() {
  const only = process.argv.slice(2)
  const ids = only.length ? only : Object.keys(CASES)
  console.log(`=== HMI CDP C 组：${ids.join(', ')} ===`)
  const browser = launchBrowser()
  const cdp = new Cdp()
  try {
    await cdp.connect()
    await cdp.waitFor(`document.querySelector('input.au-input') !== null`, 30000, 'HMI 加载')
    await sleep(1500)                     // WS 建连 + 车况首推
    for (const id of ids) {
      if (!CASES[id]) { record(id, false, '未知用例'); continue }
      try {
        const detail = await CASES[id](cdp)
        record(id, true, detail)
      } catch (e) {
        try { await cdp.screenshot(`${id}-FAIL`) } catch { /* ignore */ }
        record(id, false, String(e.message || e).slice(0, 200))
      }
      await sleep(1500)                   // 用例间隔，避免上一轮尾帧串台
    }
  } finally {
    try { browser.kill() } catch { /* ignore */ }
  }
  const pass = results.filter((r) => r.ok).length
  console.log(`\n=== ${pass}/${results.length} 通过 ===`)
  process.exit(pass === results.length ? 0 : 1)
}

main()
