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
  // C10 只读选品卡 + 商品图（2026-08-13）：跨轮门店锚定 + luckin.menu + image_hosts。
  // **不创建任何订单**——menu 与 prepare 全是只读工具；点一下商品只走到预览与确认气泡，
  // 用例不点确认。图片那条断言看的是 naturalWidth 而不是 src 存在：
  // 「卡里写了个图链」和「用户真的看见了图」是两件事，车机上一张裂图比没有图更糟。
  async C10(cdp) {
    // 等词必须是**只有助手会说**的：等「瑞幸」会命中用户自己那条消息，于是第二句
    // 立刻发出去把第一轮打断（实测 turn1 status=cancelled、焦点没落盘，第二轮无从锚定）。
    // 门店查询依赖真实 provider，偶发降级；重试一次，并把降级与卡片缺陷**分开报**——
    // 否则前置不可用会被读成本用例的结论。
    let listed = false
    for (let attempt = 0; attempt < 2 && !listed; attempt += 1) {
      await cdp.typeAndSend('帮我查一下附近的瑞幸咖啡')
      try {
        await cdp.waitFor(
          `document.body.innerText.includes('为您找到')`, 45000, '瑞幸门店列表')
        listed = true
      } catch (e) {
        const degraded = await cdp.eval(
          `document.body.innerText.includes('周边搜索服务暂时不可用')`)
        if (!degraded) throw e
        await sleep(8000)
      }
    }
    if (!listed) throw new Error('前置门店查询降级（provider 不可用），非选品卡结论')
    await cdp.screenshot('C10-store-list')

    await cdp.typeAndSend('在最近那家看看有什么可以点的')
    await cdp.waitFor(
      `document.body.innerText.includes('要哪一款')`, 90000, '选品卡话术')
    // 卡真的渲染成按钮（不是只在气泡里念了一遍）
    const optionCount = await cdp.eval(
      // 判据刻意不写正则：这串要经**模板字符串**送进浏览器，`\d` 会被模板吃掉
      // 变成 /d+.d{2} 元/ ——实测静默匹配 0 条（不报错，是判错）。用零反斜杠的写法。
      `[...document.querySelectorAll('button')]
        .filter(b => b.textContent.includes('元') && /[0-9]/.test(b.textContent)).length`)
    if (!optionCount || optionCount < 1) throw new Error(`选品按钮数=${optionCount}`)

    // 图是异步下载的（真机实测每张 ~148KB），渲染完立刻查 naturalWidth 必然是 0。
    // 断言「真加载出来」而不是「src 写对了」：车机上一张裂图比没有图更糟，
    // 而 src 正确、图却始终不显示，恰恰是最容易漏过去的那种坏体验。
    await cdp.waitFor(
      `[...document.querySelectorAll('img')]
         .filter(i => i.src.includes('luckincoffeecdn.com'))
         .some(i => i.complete && i.naturalWidth > 0)`,
      25000, '商品图真的加载出来')
    const imgs = await cdp.eval(`JSON.stringify(
      [...document.querySelectorAll('img')]
        .filter(i => i.src.includes('luckincoffeecdn.com'))
        .map(i => ({ ok: i.complete && i.naturalWidth > 0, https: i.src.startsWith('https://') })))`)
    const shots = JSON.parse(imgs)
    if (!shots.length) throw new Error('选品卡没有渲染出任何商品图')
    if (!shots.every((i) => i.https)) throw new Error('存在非 https 商品图')
    const loaded = shots.filter((i) => i.ok).length
    await cdp.screenshot('C10-merchant-choices')

    // 点一款 → 帧是卡自带 send_text，且**不是**确认帧（确认只走全局气泡）
    const t0 = Date.now()
    const label = await cdp.eval(
      `[...document.querySelectorAll('button')]
        .filter(b => b.textContent.includes('元') && /[0-9]/.test(b.textContent))[0]
        .innerText.split(String.fromCharCode(10))[0].trim()`)
    await cdp.clickButtonByText(label)
    const frame = await cdp.waitSentFrame(
      (d) => typeof d.text === 'string' && d.text.includes(label),
      10000, t0, '选品帧')
    if (frame.is_confirmation === true) {
      throw new Error('卡内选品不得是确认帧——确认只能走全局确认气泡')
    }
    await cdp.screenshot('C10-after-pick')

    // 点完之后**还能不能继续对话** —— 这正是 2026-08-13 被漏掉的盲区：
    // 那次只验到「卡片渲染出来」，而 bug 恰好活在卡片之后（拒绝走 NEED_SLOT 挂起会话，
    // 后续每一句被当补槽答案吞掉，问麦当劳答瑞幸）。
    // 判据不看话术内容，只看**下一句有没有被独立处理**：换个完全无关的域，
    // 回复必须落在那个域上。
    await sleep(1500)
    const t1 = Date.now()
    await cdp.typeAndSend('今天天气怎么样')
    await cdp.waitSentFrame(
      (d) => d.text === '今天天气怎么样', 10000, t1, '续接帧')
    await cdp.waitFor(
      `document.body.innerText.includes('气温') || document.body.innerText.includes('天气')
       || document.body.innerText.includes('温度') || document.body.innerText.includes('℃')`,
      60000, '选品之后仍能正常换话题（会话没有被挂起吞掉）')
    await cdp.screenshot('C10-followup-alive')
    return `选品卡 ${optionCount} 款 / 商品图 ${loaded}/${shots.length} 张真加载 / 帧「${frame.text}」非确认帧 / 选品后换话题仍正常`
  },

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

  // ── QA 卡 Q1/Q3/Q4 的 HMI 半边（阶段 2）──────────────────────────
  // 这三张**必须在这条车道跑**：Q3 的归属与 Q4 的位置闸都活在客户端 JS 里，
  // WS 探针是从闸后面进来的，跑多少轮都是假绿（卡 §4 出口条件）。

  // C11 · Q1-B/C：确认帧带 operation_id + 两条挂起并存、先来那条仍可确认。
  async C11(cdp) {
    await debugVehicle('gear', 'P'); await debugVehicle('speed_kmh', 0)
    const t0 = Date.now()
    await cdp.typeAndSend('打开后备箱')
    await cdp.waitFor(
      `[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '确认')`,
      30000, '第一条确认条')
    // 后端下发了寻址键（入帧证据；HMI 有没有用它由下面的出帧断言证明）
    const first = await cdp.waitReceivedFrame(
      (d) => d.type === 'final' && d.need_confirm === true && !!d.operation_id,
      15000, t0, '带 operation_id 的挂起 final')

    // 第二件危险动作 → 第二条挂起。单槽时代这一步会把第一条覆盖掉。
    const t1 = Date.now()
    await cdp.typeAndSend('把充电口盖打开')
    const second = await cdp.waitReceivedFrame(
      (d) => d.type === 'final' && d.need_confirm === true &&
        !!d.operation_id && d.operation_id !== first.operation_id,
      30000, t1, '第二条挂起 final')

    // **两条确认条同时在屏**——这是 Q1-C 唯一在 UI 上看得见的产物
    const bars = await cdp.eval(
      `[...document.querySelectorAll('button')].filter(b => b.textContent.trim() === '确认').length`)
    if (bars < 2) throw new Error(`同屏确认条数=${bars}（期望 ≥2）`)
    await cdp.screenshot('C11-two-confirm-bars')

    // 点**第一条**（更早那条）的确认 → 出帧必须带它自己的 operation_id
    const t2 = Date.now()
    await cdp.eval(`(() => {
      const b = [...document.querySelectorAll('button')].filter(x => x.textContent.trim() === '确认')[0]
      b.click(); return true
    })()`)
    const frame = await cdp.waitSentFrame(
      (d) => d.is_confirmation === true, 10000, t2, '确认帧')
    if (frame.operation_id !== first.operation_id) {
      throw new Error(
        `确认帧打给了 ${frame.operation_id}，应为更早那条 ${first.operation_id}`)
    }
    await sleep(4000)
    const st = await vehicleState()
    if (st.trunk !== 'open') throw new Error(`trunk=${st.trunk}（第一条挂起没被执行）`)

    // 另一条挂起原样活着（确认条还在）
    const left = await cdp.eval(
      `[...document.querySelectorAll('button')].filter(b => b.textContent.trim() === '确认').length`)
    if (left < 1) throw new Error('确认掉一条后，另一条挂起的确认条也消失了')
    await cdp.clickButtonByText('取消')
    await cdp.typeAndSend('关闭后备箱')            // 复位
    await sleep(2500)
    return `两条挂起并存；确认帧 operation_id=${first.operation_id.slice(0, 10)}…；第二条 ${second.operation_id.slice(0, 10)}… 未受影响`
  },

  // C12 · Q3：抢发并发归属。三请求快速连发，每个用户轮各收自己 request_id 的帧，
  // 且**没有任何气泡卡在「思考中」**（旧实现单槽看门狗被后来者清掉 → 永久转圈）。
  async C12(cdp) {
    const t0 = Date.now()
    await cdp.typeAndSend('帮我查一下深圳明天的天气')
    await sleep(600)                          // 上一轮还在生成
    await cdp.typeAndSend('讲个笑话')
    await sleep(600)
    await cdp.typeAndSend('现在几点了')

    // 三轮各自发出去，且各带**不同**的 request_id
    const sent = cdp.sentFrames.filter((f) => f.ts >= t0 && typeof f.data.text === 'string')
    const ids = sent.map((f) => f.data.request_id).filter(Boolean)
    if (ids.length < 3) throw new Error(`带 request_id 的出帧只有 ${ids.length} 条`)
    if (new Set(ids).size !== ids.length) throw new Error('request_id 撞号')

    // 网关把 id 盖回每一帧
    await cdp.waitReceivedFrame(
      (d) => d.type === 'final' && !!d.request_id, 60000, t0, '带 request_id 的入帧')
    await sleep(20000)                        // 给最慢那轮留时间
    // 「思考中」气泡的 DOM 签名 = ThinkingInline 那句「正在思考…」
    const stuck = await cdp.eval(`(() => {
      const marker = String.fromCharCode(27491,22312,24605,32771) + String.fromCharCode(8230)
      return [...document.querySelectorAll('span')]
        .filter(s => s.textContent.trim() === marker).length
    })()`)
    await cdp.screenshot('C12-concurrent-attribution')
    // 判据用**形态**不用文案：还在转圈的气泡数（§4.3 话术层只用形态判据）
    if (stuck > 0) throw new Error(`仍有 ${stuck} 个气泡卡在「思考中」`)
    const finals = cdp.recvFrames.filter(
      (f) => f.ts >= t0 && f.data.type === 'final').map((f) => f.data.request_id)
    const covered = ids.filter((id) => finals.includes(id) ||
      cdp.recvFrames.some((f) => f.data.type === 'cancelled' && f.data.request_id === id))
    if (covered.length < ids.length) {
      throw new Error(`${ids.length - covered.length} 轮既无 final 也无 cancelled——它没人管`)
    }
    return `${ids.length} 轮各自归属，零卡死气泡`
  },

  // C13 · Q4：位置前置闸收窄。四组只看**发没发出去**——被闸拦下的句子根本不会出帧，
  // 这正是 WS 探针看不见的那一层（I-007 整句被吞）。
  async C13(cdp) {
    // 前置：关掉定位设置，让闸走「征询」分支（这才是会吞整句的那条路）
    await cdp.eval(`(() => {
      const k = 'cockpit.settings.v1'
      const cur = JSON.parse(localStorage.getItem(k) || '{}')
      localStorage.setItem(k, JSON.stringify({ ...cur, locationEnabled: false }))
      return true
    })()`)
    await cdp.send('Page.reload')
    await sleep(2500)
    await cdp.waitFor(`document.querySelector('input.au-input') !== null`, 30000, 'HMI 重载')

    const mustSend = async (text, label) => {
      const t = Date.now()
      await cdp.typeAndSend(text)
      await cdp.waitSentFrame((d) => d.text === text, 8000, t, label)
      await sleep(1200)
    }
    const mustAsk = async (text, label) => {
      const t = Date.now()
      await cdp.typeAndSend(text)
      await sleep(1500)
      const leaked = cdp.sentFrames.some((f) => f.ts >= t && f.data.text === text)
      if (leaked) throw new Error(`${label}：本该征询定位，却直接发了出去`)
      await cdp.clickButtonByText('取消')       // 收掉征询条
      await sleep(800)
    }

    await mustSend('打开充电口', 'Q4① 车控对象')
    await mustSend('取消当前导航', 'Q4③ 取消句')
    await mustSend('查深圳欢乐海岸周边停车场', 'Q4② 显式地点')
    await mustSend('关空调，查深圳天气，再看看股票', 'Q4④ 多意图句')
    // 对照：真正没有地点线索的就近查询仍然征询（**不是把闸拆了**）
    await mustAsk('附近有什么好吃的', 'Q4 对照')
    await cdp.screenshot('C13-location-gate')
    return '四组直发 + 一组仍征询（闸收窄而非拆除）'
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
