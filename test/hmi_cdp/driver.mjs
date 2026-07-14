// HMI CDP 驱动 —— 宿主 Node ≥22 零依赖（全局 WebSocket + fetch），headless Edge/Chrome。
//
// 职责（设计 docs/design/2026-07-14-journey-e2e-test-system.md §4.2 L4 层）：
//   验证协议层（test/e2e_journeys.py）模拟不到的 HMI 自有语义——
//   渲染、前端文本合成、序号改写（App.tsx send() 五层拦截）、meta 透传、确认条。
//   核心断言手段：Network.webSocketFrameSent 实拦 HMI→edge-gateway 的出帧。
//
// 前置：make up 全栈在跑；hmi 容器 5173（宿主 vite 若占 5173 先停，历史坑）。
// 用法：node test/hmi_cdp/run_cases.mjs [caseId...]
import { spawn } from 'node:child_process'
import { mkdtempSync, existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
export const SHOTS_DIR = join(HERE, 'shots')
export const HMI_URL = process.env.CDP_HMI_URL || 'http://localhost:5173'
export const COLLECTOR = process.env.CDP_COLLECTOR || 'http://localhost:8092'
const PORT = Number(process.env.CDP_PORT || 9223)

const BROWSERS = [
  process.env.CDP_BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean)

export function launchBrowser() {
  const exe = BROWSERS.find((p) => existsSync(p))
  if (!exe) throw new Error('未找到 Edge/Chrome，可设 CDP_BROWSER 指定路径')
  const profile = mkdtempSync(join(tmpdir(), 'hmi-cdp-'))
  const child = spawn(exe, [
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--headless=new', '--no-first-run', '--disable-gpu', '--mute-audio',
    '--autoplay-policy=no-user-gesture-required',
    '--window-size=1920,1080',
    HMI_URL,
  ], { stdio: 'ignore' })
  return child
}

async function pageTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()
      const page = list.find((t) => t.type === 'page' && t.url.startsWith(HMI_URL))
      if (page) return page
    } catch { /* 浏览器还没起来 */ }
    await sleep(500)
  }
  throw new Error('CDP 目标页 60×500ms 内未就绪')
}

export class Cdp {
  constructor() {
    this.id = 0
    this.pending = new Map()
    this.sentFrames = []      // HMI→gateway 出帧（JSON 解析后）——L4 层的核心证据流
    this.recvFrames = []
  }

  async connect() {
    const target = await pageTarget()
    this.ws = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((res, rej) => { this.ws.onopen = res; this.ws.onerror = rej })
    this.ws.onmessage = (ev) => this._onMessage(String(ev.data))
    await this.send('Runtime.enable')
    await this.send('Page.enable')
    await this.send('Network.enable')
    // 定位三件套：headless 无真实定位，「附近」类用例（C2a/C2b）需要——
    // 授权 + 坐标 override（深圳南山，与旅程语料同点）+ 预置 locationEnabled 设置后刷新。
    try {
      await this.send('Browser.grantPermissions',
        { permissions: ['geolocation'], origin: HMI_URL })
    } catch { /* 旧内核无此方法则靠 override 兜底 */ }
    await this.send('Emulation.setGeolocationOverride',
      { latitude: 22.5333, longitude: 113.9505, accuracy: 10 })
    await this.eval(`(() => {
      const k = 'cockpit.settings.v1'
      const cur = JSON.parse(localStorage.getItem(k) || '{}')
      localStorage.setItem(k, JSON.stringify({ ...cur, locationEnabled: true }))
      return true
    })()`)
    await this.send('Page.reload')
    await sleep(1500)
  }

  _onMessage(raw) {
    const msg = JSON.parse(raw)
    if (msg.id && this.pending.has(msg.id)) {
      const { res, rej } = this.pending.get(msg.id)
      this.pending.delete(msg.id)
      msg.error ? rej(new Error(msg.error.message)) : res(msg.result)
      return
    }
    if (msg.method === 'Network.webSocketFrameSent') {
      try { this.sentFrames.push({ ts: Date.now(), data: JSON.parse(msg.params.response.payloadData) }) }
      catch { /* 非 JSON 帧（如 ASR 二进制）忽略 */ }
    } else if (msg.method === 'Network.webSocketFrameReceived') {
      try { this.recvFrames.push({ ts: Date.now(), data: JSON.parse(msg.params.response.payloadData) }) }
      catch { /* ignore */ }
    }
  }

  send(method, params = {}) {
    const id = ++this.id
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej })
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }

  async eval(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    })
    if (r.exceptionDetails) throw new Error(`eval 异常: ${r.exceptionDetails.text} | ${expr.slice(0, 120)}`)
    return r.result?.value
  }

  // 轮询 DOM/JS 条件直到真值。expr 必须是**求值为布尔/真值**的表达式。
  async waitFor(expr, timeoutMs = 20000, label = '') {
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutMs) {
      if (await this.eval(expr)) return true
      await sleep(400)
    }
    throw new Error(`waitFor 超时(${timeoutMs}ms): ${label || expr.slice(0, 100)}`)
  }

  // 按可见文本点按钮（确认条/卡按钮无稳定 class——文本即契约，不改产品代码加 testid）
  async clickButtonByText(text) {
    const ok = await this.eval(`(() => {
      const b = [...document.querySelectorAll('button')]
        .find(x => x.textContent.trim().includes(${JSON.stringify(text)}))
      if (!b) return false
      b.click(); return true
    })()`)
    if (!ok) throw new Error(`按钮不存在: ${text}`)
  }

  // 打字进 Composer 并发送（React 受控输入须走原生 setter + input 事件）
  async typeAndSend(text) {
    await this.eval(`(() => {
      const el = document.querySelector('input.au-input')
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
      setter.call(el, ${JSON.stringify(text)})
      el.dispatchEvent(new Event('input', { bubbles: true }))
      return true
    })()`)
    await sleep(80)
    await this.eval(`document.querySelector('button.au-send').click()`)
  }

  // 等一条满足谓词的出帧（sinceTs 起）。pred 接收解析后的 JSON。
  async waitSentFrame(pred, timeoutMs = 15000, sinceTs = 0, label = '出帧') {
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutMs) {
      const hit = this.sentFrames.find((f) => f.ts >= sinceTs && pred(f.data))
      if (hit) return hit.data
      await sleep(200)
    }
    throw new Error(`等${label}超时：近帧=${JSON.stringify(this.sentFrames.slice(-3).map(f => f.data.text || f.data.type)).slice(0, 200)}`)
  }

  async bodyText() {
    return await this.eval('document.body.innerText')
  }

  async screenshot(name) {
    if (!existsSync(SHOTS_DIR)) mkdirSync(SHOTS_DIR, { recursive: true })
    const { data } = await this.send('Page.captureScreenshot', { format: 'png' })
    const p = join(SHOTS_DIR, `${name}.png`)
    writeFileSync(p, Buffer.from(data, 'base64'))
    return p
  }
}

// collector 车况面（与 e2e_journeys 同源断言面）
export async function vehicleState() {
  return await (await fetch(`${COLLECTOR}/api/vehicle/state`)).json()
}
export async function debugVehicle(key, value) {
  await fetch(`${COLLECTOR}/api/debug/vehicle`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, value }),
  })
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
