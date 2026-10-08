// Offline I1 evidence. Requires only Vite and a local Chromium browser.
// No service requests: the token fixture has no App/WebSocket/hardware effects.
import { spawn, execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve, join } from 'node:path'
import assert from 'node:assert/strict'
import { Cdp, sleep } from './driver.mjs'

const root = resolve(import.meta.dirname, '../..')
const out = join(root, '.artifacts/hmi-visual-v2/i1')
const base = process.env.HMI_VISUAL_URL || 'http://127.0.0.1:5188'
const port = 9338
const exe = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(existsSync)
assert.ok(exe, 'Edge is required')
mkdirSync(out, { recursive: true })
const browser = spawn(exe, ['--headless=new', '--no-first-run', '--force-device-scale-factor=1',
  `--remote-debugging-port=${port}`, `--user-data-dir=${join(out, 'browser-profile')}`, 'about:blank'],
{ windowsHide: true, stdio: 'ignore' })
const cdp = new Cdp()
try {
  let pages
  for (let i = 0; i < 60; i++) {
    try { pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (pages.length) break } catch {}
    await sleep(250)
  }
  assert.ok(pages?.length, 'headless browser did not start')
  cdp.ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl)
  await new Promise((res, rej) => { cdp.ws.onopen = res; cdp.ws.onerror = rej })
  cdp.ws.onmessage = ev => cdp._onMessage(String(ev.data))
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')
  const snapshot = JSON.parse(readFileSync(join(root, 'hmi/design/visual-v2.tokens.json'), 'utf8'))
  const sizes = snapshot.collections.find(c => c.name === 'Size')
  const evidence = []
  for (const theme of ['dark', 'light']) for (const [index, mode] of sizes.modes.entries()) {
    const drive = index === 1 || index === 3 ? 'on' : 'off'
    const font = index >= 2 ? 'large' : 'normal'
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 948, height: 1900, deviceScaleFactor: 1, mobile: false })
    await cdp.send('Page.navigate', { url: `${base}/?tokens&theme=${theme}&drive=${drive}&font=${font}` })
    await cdp.waitFor(`document.querySelector('[data-testid="token-specimen"]') && document.documentElement.dataset.font === '${font}' && document.documentElement.dataset.drive === '${drive}'`, 10000)
    await cdp.eval('document.fonts.ready.then(() => true)')
    const rendered = await cdp.eval(`(() => {
      const css = getComputedStyle(document.documentElement)
      return { tokens: Object.fromEntries(${JSON.stringify(sizes.variables.map(v => v[1].slice(4, -1)))}.map(k => [k, css.getPropertyValue(k).trim()])),
        fonts: [...document.fonts].map(f => ({family:f.family, status:f.status})),
        requests: performance.getEntriesByType('resource').map(r => r.name),
        body: getComputedStyle(document.querySelector('[data-type="body"]')).fontSize }
    })()`)
    for (const [name, syntax, ...values] of sizes.variables) assert.equal(rendered.tokens[syntax.slice(4, -1)], `${values[index]}px`, `${theme}/${mode}/${name}`)
    assert.ok(rendered.fonts.some(f => f.family === 'Inter' && f.status === 'loaded'))
    assert.ok(rendered.fonts.some(f => f.family === 'JetBrains Mono' && f.status === 'loaded'))
    assert.ok(rendered.requests.every(url => url.startsWith(base)), 'all fixture assets must be local')
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    const file = `${theme}-${drive}-${font}.png`
    writeFileSync(join(out, file), Buffer.from(shot.data, 'base64'))
    evidence.push({ theme, mode, file, body: rendered.body, tokens: rendered.tokens, fonts: rendered.fonts })
  }
  const sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim()
  // Exercise the real App wiring with an isolated in-browser socket, never a backend.
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.WebSocket = class {
      static OPEN=1; static CLOSED=3;
      constructor(){ this.readyState=0; window.__visualSocket=this; setTimeout(()=>{this.readyState=1;this.onopen?.({})},10) }
      send(){} close(){this.readyState=3}
    };
    localStorage.setItem('cockpit.settings.v1', JSON.stringify({ttsEnabled:false, autoplay:false}));
  ` })
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false })
  await cdp.send('Page.navigate', { url: `${base}/?settings=display` })
  await cdp.waitFor("window.__visualSocket?.readyState===1 && !!document.querySelector('[role=switch]')")
  const frame = async driving => cdp.eval(`window.__visualSocket.onmessage({data:JSON.stringify({type:'final',speech:'',driving:${driving}})}); true`)
  await frame(true)
  await cdp.waitFor("document.documentElement.dataset.drive==='on'")
  await cdp.eval("[...document.querySelectorAll('[role=switch]')].at(-1).click(); true")
  await cdp.waitFor("document.documentElement.dataset.drive==='off'")
  await frame(true)
  await sleep(100)
  assert.equal(await cdp.eval('document.documentElement.dataset.drive'), 'off', 'dismissal must survive same-segment final frames')
  await frame(false)
  await sleep(10)
  await frame(true)
  await cdp.waitFor("document.documentElement.dataset.drive==='on'")
  await frame(false)
  await sleep(100)
  assert.equal(await cdp.eval('document.documentElement.dataset.drive'), 'on', 'the 30 s grace must be active')
  await cdp.waitFor("document.documentElement.dataset.drive==='off'", 31000, 'continuous false exits after 30 s')
  console.log('PASS: final-only driving -> dismiss segment -> next segment enters -> 30 s exit (real App, isolated socket)')
  writeFileSync(join(out, 'browser-evidence.json'), JSON.stringify({ baseSha: sha, dirty: true, capturedAt: new Date().toISOString(), evidence }, null, 2))
  console.log(`PASS: 8 theme/drive/font combinations, all 27 Size tokens, two local fonts; ${out}`)
} finally {
  if (cdp.ws?.readyState === WebSocket.OPEN) { try { await cdp.send('Browser.close') } catch {} cdp.ws.close() }
  else browser.kill()
}
