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
const REPO_ROOT = join(HERE, '..', '..')
export const SHOTS_DIR = join(HERE, 'shots')
export const HMI_URL = process.env.CDP_HMI_URL || 'http://localhost:5173'
export const COLLECTOR = process.env.CDP_COLLECTOR || 'http://localhost:8092'
export const C14_ARTIFACT = process.env.CDP_C14_ARTIFACT || join(
  REPO_ROOT, '.artifacts', 'dev-stack-verifications', 'hmi-cdp-c14.json')
const PORT = Number(process.env.CDP_PORT || 9223)
const coordinate = (name, fallback, min, max) => {
  const raw = String(process.env[name] || '').trim()
  if (!raw) return fallback
  const value = Number(raw)
  if (!Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${name} 不是有效坐标`)
  }
  return value
}
const LATITUDE = coordinate('CDP_LATITUDE', 22.5333, -90, 90)
const LONGITUDE = coordinate('CDP_LONGITUDE', 113.9505, -180, 180)

const BROWSERS = [
  process.env.CDP_BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean)

const FULL_SHA_RE = /^[0-9a-f]{40}$/

function normalizedBaseUrl(value) {
  try {
    const url = new URL(String(value || ''))
    url.search = ''
    url.hash = ''
    url.pathname = url.pathname.replace(/\/+$/, '') || '/'
    return url.toString().replace(/\/$/, '')
  } catch {
    return ''
  }
}

function collectorBaseFromStatus(endpoint) {
  try {
    const url = new URL(String(endpoint && endpoint.url || ''))
    url.search = ''
    url.hash = ''
    url.pathname = url.pathname.replace(/\/healthz\/?$/, '') || '/'
    return url.toString().replace(/\/$/, '')
  } catch {
    return ''
  }
}

export function validateCloudReleaseSnapshot(
  snapshot,
  expectedSha,
  { collectorUrl = COLLECTOR } = {},
) {
  const failures = []
  const expected = String(expectedSha || '').trim().toLowerCase()
  const release = String(snapshot && snapshot.release_sha || '').trim().toLowerCase()
  const endpoints = Array.isArray(snapshot && snapshot.endpoint_results)
    ? snapshot.endpoint_results : []
  if (!FULL_SHA_RE.test(expected)) failures.push('CDP_EXPECTED_SHA 必须是完整 40 位 SHA')
  if (!snapshot || snapshot.target !== 'cloud') failures.push('dev_stack target 不是 cloud')
  if (!snapshot || snapshot.status !== 'ok') failures.push('dev_stack status 不是 ok')
  if (!FULL_SHA_RE.test(release)) failures.push('release_sha 不是完整 40 位 SHA')
  if (FULL_SHA_RE.test(expected) && FULL_SHA_RE.test(release) && release !== expected) {
    failures.push(`release_sha=${release} 与 expected=${expected} 不一致`)
  }
  if (Number(snapshot && snapshot.healthy_endpoints || 0) !== 5 ||
      endpoints.length !== 5 || endpoints.some((item) => !item || item.status !== 'healthy')) {
    failures.push(`云端端点不是 5/5 healthy：${Number(snapshot && snapshot.healthy_endpoints || 0)}/5`)
  }
  if (Array.isArray(snapshot && snapshot.warnings) && snapshot.warnings.length) {
    failures.push(`dev_stack status 带 warning：${snapshot.warnings.join('；')}`)
  }
  const collector = endpoints.find((item) => item && item.name === 'collector')
  const expectedCollector = collectorBaseFromStatus(collector)
  const actualCollector = normalizedBaseUrl(collectorUrl)
  if (!expectedCollector || !actualCollector || expectedCollector !== actualCollector) {
    failures.push('CDP_COLLECTOR 未绑定 dev_stack 的 cloud collector')
  }
  return failures
}

function devStackJson(args, timeoutMs = 60000) {
  const python = process.env.CDP_PYTHON || process.env.PYTHON || 'python'
  const script = join(REPO_ROOT, 'scripts', 'dev_stack.py')
  return new Promise((resolve, reject) => {
    const child = spawn(python, [script, ...args], {
      cwd: REPO_ROOT, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      try { child.kill() } catch { /* ignore */ }
      reject(new Error(`dev_stack ${args.join(' ')} 超时`))
    }, timeoutMs)
    const append = (current, chunk) => {
      const next = current + String(chunk || '')
      if (Buffer.byteLength(next, 'utf8') > 1024 * 1024) {
        throw new Error('dev_stack 输出超过 1 MiB')
      }
      return next
    }
    child.stdout.on('data', (chunk) => {
      try { stdout = append(stdout, chunk) } catch (error) { child.kill(); reject(error) }
    })
    child.stderr.on('data', (chunk) => {
      try { stderr = append(stderr, chunk) } catch (error) { child.kill(); reject(error) }
    })
    child.on('error', (error) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
      let payload = null
      try { payload = JSON.parse(lines.at(-1) || '') } catch { /* handled below */ }
      if (code !== 0 || !payload || typeof payload !== 'object') {
        reject(new Error(
          `dev_stack ${args.join(' ')} 失败(rc=${code}): ${stderr.trim().slice(0, 300)}`))
        return
      }
      resolve(payload)
    })
  })
}

export async function cloudReleaseSnapshot(expectedSha) {
  const expected = String(expectedSha || '').trim().toLowerCase()
  if (!FULL_SHA_RE.test(expected)) {
    throw new Error('CDP_EXPECTED_SHA 必须是完整 40 位 SHA')
  }
  const target = await devStackJson(['target', 'show'])
  if (!target || target.target !== 'cloud') throw new Error('dev_stack target 不是 cloud')
  const snapshot = await devStackJson(['status'])
  const failures = validateCloudReleaseSnapshot(
    snapshot, expected, { collectorUrl: COLLECTOR })
  if (failures.length) throw new Error(failures.join('；'))
  return snapshot
}

export function redactedReleaseSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return null
  return {
    target: snapshot.target,
    status: snapshot.status,
    release_sha: snapshot.release_sha,
    healthy_endpoints: snapshot.healthy_endpoints,
    endpoint_results: Array.isArray(snapshot.endpoint_results)
      ? snapshot.endpoint_results.map((item) => ({
        name: item && item.name, status: item && item.status,
        http_status: item && item.http_status,
      })) : [],
    warnings: Array.isArray(snapshot.warnings) ? snapshot.warnings : [],
  }
}

export function writeC14Artifact(payload) {
  mkdirSync(dirname(C14_ARTIFACT), { recursive: true })
  writeFileSync(C14_ARTIFACT, JSON.stringify(payload, null, 2), 'utf8')
  return C14_ARTIFACT
}

const AUDIO_PROBE_SOURCE = String.raw`(() => {
  if (window.__qaAudioProbeInstalled) return
  window.__qaAudioProbeInstalled = true
  const probe = window.__qaAudioProbe = {
    starts: 0, stops: 0, ended: 0, active: 0, buffers: [],
  }
  const proto = window.AudioBufferSourceNode && window.AudioBufferSourceNode.prototype
  if (!proto) return
  const start = proto.start
  const stop = proto.stop
  proto.start = function (...args) {
    let peak = 0
    let nonzero = 0
    let samples = 0
    try {
      const channel = this.buffer && this.buffer.getChannelData(0)
      samples = channel ? channel.length : 0
      if (channel) {
        for (let i = 0; i < channel.length; i += 1) {
          const abs = Math.abs(channel[i])
          if (abs > peak) peak = abs
          if (abs >= 0.0005) nonzero += 1
        }
      }
    } catch { /* evidence stays zero and the case fails closed */ }
    probe.starts += 1
    probe.active += 1
    probe.buffers.push({
      samples,
      sampleRate: this.buffer ? this.buffer.sampleRate : 0,
      durationMs: this.buffer ? Math.round(this.buffer.duration * 1000) : 0,
      peak,
      nonzeroRatio: samples ? nonzero / samples : 0,
      startedAt: performance.now(),
    })
    this.addEventListener('ended', () => {
      probe.ended += 1
      probe.active = Math.max(0, probe.active - 1)
    }, { once: true })
    return start.apply(this, args)
  }
  proto.stop = function (...args) {
    probe.stops += 1
    return stop.apply(this, args)
  }
})()`

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
    this.wsConnections = new Map()
    this.wsFrames = []        // 全部 WS（含 TTS 二进制）的 URL/方向/字节证据
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
      { latitude: LATITUDE, longitude: LONGITUDE, accuracy: 10 })
    // ⚠ `pageTarget()` 认的是 target 的 url，而**文档可能还停在 about:blank**——
    // 那个 origin 上碰 localStorage 直接抛 SecurityError，整趟 CDP 在 connect 就崩，
    // 一条用例都没跑就退出（读起来像用例失败，其实是驱动没就绪）。等真 origin 再写。
    // 探针自己吞异常：opaque origin 上**读** localStorage 就抛，而 `eval` 遇到
    // exceptionDetails 是直接 rethrow 的，不 try 的话 waitFor 第一轮就崩、退化成没有重试。
    await this.waitFor(
      `(() => { try { return location.origin !== 'null' && !!localStorage } catch { return false } })()`,
      15000, '文档 origin 就绪')
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
    if (msg.method === 'Network.webSocketCreated') {
      this.wsConnections.set(msg.params.requestId, msg.params.url)
      return
    }
    if (msg.method === 'Network.webSocketFrameSent' ||
        msg.method === 'Network.webSocketFrameReceived') {
      const direction = msg.method.endsWith('Sent') ? 'sent' : 'received'
      const response = msg.params.response || {}
      const payloadData = String(response.payloadData || '')
      const opcode = Number(response.opcode)
      let data = null
      if (opcode === 1) {
        try { data = JSON.parse(payloadData) } catch { /* non-JSON text frame */ }
      }
      const bytes = opcode === 1
        ? Buffer.byteLength(payloadData, 'utf8')
        : Buffer.from(payloadData, 'base64').length
      const frame = {
        ts: Date.now(), direction, requestId: msg.params.requestId,
        url: this.wsConnections.get(msg.params.requestId) || '',
        opcode, bytes, data,
      }
      this.wsFrames.push(frame)
      if (data && direction === 'sent') this.sentFrames.push({ ts: frame.ts, data })
      if (data && direction === 'received') this.recvFrames.push({ ts: frame.ts, data })
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

  // 等一条满足谓词的入帧，确保按钮不止“发出去了”，后端业务也真实收口。
  async waitReceivedFrame(pred, timeoutMs = 30000, sinceTs = 0, label = '入帧') {
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutMs) {
      const hit = this.recvFrames.find((f) => f.ts >= sinceTs && pred(f.data))
      if (hit) return hit.data
      await sleep(200)
    }
    throw new Error(`等${label}超时：近帧=${JSON.stringify(this.recvFrames.slice(-3).map(f => f.data.type)).slice(0, 200)}`)
  }

  async waitWsFrame(pred, timeoutMs = 30000, sinceTs = 0, label = 'WebSocket 帧') {
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutMs) {
      const hit = this.wsFrames.find((frame) => frame.ts >= sinceTs && pred(frame))
      if (hit) return hit
      await sleep(100)
    }
    const recent = this.wsFrames.slice(-5).map((frame) => ({
      direction: frame.direction, url: frame.url, opcode: frame.opcode,
      type: frame.data && frame.data.type, bytes: frame.bytes,
    }))
    throw new Error(`等${label}超时：近帧=${JSON.stringify(recent).slice(0, 500)}`)
  }

  async installAudioProbe() {
    await this.send('Page.addScriptToEvaluateOnNewDocument', {
      source: AUDIO_PROBE_SOURCE,
    })
    await this.eval(AUDIO_PROBE_SOURCE)
  }

  async audioProbe() {
    const raw = await this.eval(
      `JSON.stringify(window.__qaAudioProbe || { starts: 0, stops: 0, ended: 0, active: 0, buffers: [] })`)
    return JSON.parse(raw)
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

export async function turnDetail(traceId, attempts = 12, ready = null) {
  let detail = { error: 'not found' }
  let previousFingerprint = ''
  let stableReadyReads = 0
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetch(
        `${COLLECTOR}/api/turns/${encodeURIComponent(traceId)}`)
      detail = await response.json()
      if (response.ok && detail && detail.error !== 'not found' &&
          (!ready || ready(detail))) {
        const fingerprint = JSON.stringify({
          turn: detail.turn || null,
          spans: (detail.spans || []).map((span) => ({
            id: span.id, span_id: span.span_id, node: span.node,
            status: span.status, attrs: span.attrs,
          })),
          llm_calls: (detail.llm_calls || []).map((call) => ({
            id: call.id, provider: call.provider, model: call.model,
            pinned: call.pinned, fallback: call.fallback,
            status: call.status, error: call.error,
          })),
        })
        stableReadyReads = fingerprint === previousFingerprint
          ? stableReadyReads + 1 : 1
        previousFingerprint = fingerprint
        if (stableReadyReads >= 2) return detail
      } else {
        previousFingerprint = ''
        stableReadyReads = 0
      }
    } catch (error) {
      detail = { error: error && error.name ? error.name : 'fetch failed' }
    }
    if (attempt + 1 < attempts) await sleep(500)
  }
  return detail
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
