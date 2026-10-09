// Isolated browser QA. Only ?fixture= pages; external requests are blocked by CDP.
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { resolve, join } from 'node:path'
import { Cdp, sleep } from '../test/hmi_cdp/driver.mjs'

const root = resolve(import.meta.dirname, '..')
const out = join(root, '.artifacts/dashboard-visual-v2/browser')
const base = process.env.DASHBOARD_VISUAL_URL || 'http://127.0.0.1:5174'
const exe = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(existsSync)
assert.ok(exe, 'Edge is required')
mkdirSync(out, { recursive: true })
const port = 9347
const browser = spawn(exe, ['--headless=new', '--guest', '--no-first-run', '--disable-extensions', '--force-device-scale-factor=1', `--remote-debugging-port=${port}`, `--user-data-dir=${join(out, 'profile')}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
const cdp = new Cdp()
const evidence = []
const sourcePaths = [...new Set(execFileSync('git', ['ls-files', '-co', '--exclude-standard', '--', 'dashboard', 'observability/collector', 'runtime/outcome.py', 'hmi/design/visual-v2.tokens.json', 'hmi/src/components/icons.gen.ts', 'hmi/src/components/icons.custom.ts', 'mobile/src/ui/icons.local.ts'], { cwd: root, encoding: 'utf8' }).trim().split(/\r?\n/))].filter(Boolean).sort()
const sourceDigest = () => {
  const digest = createHash('sha256')
  for (const path of sourcePaths) { digest.update(path); digest.update(readFileSync(join(root, path))) }
  return digest.digest('hex')
}
const baseSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim()
const testedSourceDigest = sourceDigest()
const args = process.argv.slice(2)
const interactions = args.includes('--interactions')
const requested = args.filter(arg => arg !== '--interactions')
try {
  let target
  for (let i = 0; i < 60; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(p => p.type === 'page' && p.url === 'about:blank') } catch {}
    if (target) break
    await sleep(250)
  }
  assert.ok(target)
  cdp.ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((ok, fail) => { cdp.ws.onopen = ok; cdp.ws.onerror = fail })
  let exceptions = []
  cdp.ws.onmessage = event => {
    const message = JSON.parse(String(event.data))
    if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails?.text || 'runtime exception')
    cdp._onMessage(String(event.data))
  }
  for (const domain of ['Page', 'Runtime', 'Network']) await cdp.send(`${domain}.enable`)
  // Extra defence: fixtures themselves must never attempt a service request.
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `
    window.__externalAttempts=[];
    const originalFetch=window.fetch;
    window.fetch=(url,init)=>{const target=new URL(String(url),location.href);if(target.origin!==location.origin){window.__externalAttempts.push(target.origin);throw new Error('Offline fixture attempted an external request')}return originalFetch(url,init)};
    const NativeSocket=window.WebSocket;
    window.WebSocket=class extends NativeSocket { constructor(url,...args){const target=new URL(String(url),location.href);if(target.host!==location.host){window.__externalAttempts.push(target.host);throw new Error('Offline fixture attempted external socket')}super(url,...args)} };
  ` })
  const fixtures = requested.length ? requested : ['components', 'turns', 'live', 'logs', 'usage', 'token-first', 'token-invalid', 'token-not-configured', 'token-unreachable', 'empty', 'error', 'content-off', 'disconnected', 'missing']
  const capture = async filename => {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(out, filename + '.png'), Buffer.from(data, 'base64'))
  }
  for (const fixture of fixtures) for (const theme of ['dark', 'light']) {
    const [name, widthValue, sizeValue] = fixture.split(':')
    const width = Number(widthValue || 1920), size = sizeValue || 'workbench'
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height: width === 2560 ? 1440 : width <= 1440 ? 900 : 1080, deviceScaleFactor: 1, mobile: false })
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
    exceptions = []
    await cdp.send('Page.navigate', { url: `${base}/?fixture=${name}&theme=${theme}&size=${size}` })
    await cdp.waitFor(`document.readyState === 'complete' && new URLSearchParams(location.search).get('fixture') === ${JSON.stringify(name)} && document.documentElement.dataset.theme === ${JSON.stringify(theme)} && !!document.querySelector('main,.observatory')`, 15000)
    await cdp.eval('document.fonts.ready.then(()=>true)')
    await sleep(500)
    const state = await cdp.eval(`({ title:document.title, text:document.body.innerText.slice(0,500), external:window.__externalAttempts,
      overflow:document.documentElement.scrollWidth>innerWidth,
      nestedButtons:document.querySelectorAll('button button').length,
      unnamedButtons:[...document.querySelectorAll('button')].filter(b=>!b.textContent.trim()&&!b.getAttribute('aria-label')).length,
      failedAssets:[...document.images].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.getAttribute('src')),
      externalResources:performance.getEntriesByType('resource').filter(r=>!r.name.startsWith(location.origin)).map(r=>r.name),
      activeAnimations:document.getAnimations().filter(a=>a.playState==='running').length,
      fonts:[...document.fonts].map(f=>({family:f.family,status:f.status})),
      theme:document.documentElement.dataset.theme,size:document.documentElement.dataset.size })`)
    assert.deepEqual(state.external, [], name + ': no service traffic')
    assert.equal(state.nestedButtons, 0, name + ': nested controls')
    assert.equal(state.overflow, false, name + ': viewport overflow')
    assert.equal(state.theme, theme)
    assert.deepEqual(exceptions, [], name + ': runtime exceptions')
    assert.deepEqual(state.failedAssets, [], name + ': static asset failures')
    assert.deepEqual(state.externalResources, [], name + ': all assets are local')
    assert.equal(state.unnamedButtons, 0, name + ': controls have accessible names')
    assert.equal(state.activeAnimations, 0, name + ': reduced motion')
    const filename = `${name}-${width}-${size}-${theme}.png`
    await capture(filename.slice(0, -4))
    evidence.push({ fixture: name, width, size, theme, filename, ...state })
    console.log(`PASS ${filename}`)

    if (interactions && name === 'turns') {
      await cdp.waitFor("!!document.querySelector('.timeline-item-row')")
      await cdp.eval("[...document.querySelectorAll('.timeline-item-row')].find(b=>b.textContent.includes('step.agent:navigation')).click(); true")
      await cdp.waitFor("!!document.querySelector('.timeline-item-detail')")
      await capture(`inspector-selected-${width}-${theme}`)
      await cdp.eval("[...document.querySelectorAll('.timeline-toolbar button')].find(b=>b.textContent.includes('列表视图')).click(); true")
      await cdp.waitFor("!!document.querySelector('.timeline-table')")
      await capture(`timeline-list-${width}-${theme}`)
      await cdp.eval("[...document.querySelectorAll('.timeline-toolbar button')].find(b=>b.textContent.includes('时间线视图')).click(); true")
      for (const tab of ['规划', 'LLM', '日志', '原始 JSON']) {
        await cdp.eval(`[...document.querySelectorAll('[role=tab]')].find(b=>b.closest('.inspector-tabs')&&b.textContent.startsWith(${JSON.stringify(tab)})).click(); true`)
        await sleep(220)
        await capture(`inspector-${['规划', 'LLM', '日志', '原始 JSON'].indexOf(tab)}-${width}-${theme}`)
      }
      await cdp.eval("[...document.querySelectorAll('button')].find(b=>b.textContent==='重放对照').click(); true")
      await cdp.waitFor("!!document.querySelector('.obs-dialog')")
      assert.equal(await cdp.eval('document.activeElement.textContent'), '取消', 'replay starts at the cancel button')
      await capture(`replay-confirm-${width}-${theme}`)
      await cdp.eval("[...document.querySelectorAll('.obs-dialog button')].find(b=>b.textContent==='重放').click(); true")
      await cdp.waitFor("!!document.querySelector('.inspector-comparison .timeline') && document.querySelectorAll('.inspector-comparison .timeline').length===2")
      const ranges = await cdp.eval("[...document.querySelectorAll('.inspector-comparison .timeline')].map(e=>[e.dataset.rangeStart,e.dataset.rangeEnd])")
      assert.deepEqual(ranges[0], ranges[1], 'both replay timelines use the same scale')
      await capture(`replay-compare-${width}-${theme}`)
      if (width <= 1280) {
        await cdp.eval("const opener=document.querySelector('[aria-label=\"打开轮次列表\"]'); opener.focus(); opener.click(); true")
        await cdp.waitFor("!!document.querySelector('.turns-list[aria-modal=true]')")
        await capture(`list-drawer-${width}-${theme}`)
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 })
        await cdp.waitFor("!document.querySelector('.turns-list[aria-modal=true]')")
        assert.equal(await cdp.eval("document.activeElement.getAttribute('aria-label')"), '打开轮次列表')
      }
    }
    if (interactions && name === 'logs') {
      const before = await cdp.eval("document.querySelectorAll('.obs-log-row').length")
      await cdp.eval("document.querySelector('[aria-label=\"暂停跟随\"]').click(); true")
      await cdp.eval(`for(let i=0;i<2;i++) window.dispatchEvent(new CustomEvent('obs-fixture-log',{detail:{ts:1791475800000,service:'fixture-stream',level:'INFO',logger:'visual',msg:'same repeated fixture event',trace_id:'f17a000000000001',session_id:'fixture'}})); true`)
      await cdp.waitFor("document.body.innerText.includes('2 条新日志')")
      assert.equal(await cdp.eval("document.querySelectorAll('.obs-log-row').length"), before, 'pause freezes displayed logs')
      await capture(`logs-paused-${width}-${theme}`)
      await cdp.eval("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('2 条新日志')).click(); true")
      await cdp.waitFor(`document.querySelectorAll('.obs-log-row').length===${before + 2}`)
    }
    if (interactions && name === 'live-disabled') {
      await cdp.eval("document.querySelector('.sim-env__summary').click(); true")
      assert.equal(await cdp.eval("[...document.querySelectorAll('.sim-env fieldset input,.sim-env fieldset button')].every(e=>e.matches(':disabled'))"), true)
      await capture(`live-debug-disabled-${width}-${theme}`)
    }
    assert.deepEqual(await cdp.eval('window.__externalAttempts'), [], name + ': interactions stay offline')
  }
  assert.equal(sourceDigest(), testedSourceDigest, 'tested sources changed during visual QA')
  writeFileSync(join(out, 'evidence.json'), JSON.stringify({ baseSha, sourceDigest: testedSourceDigest, sourcePaths, dirty: true, interactions, at: new Date().toISOString(), evidence }, null, 2))
} finally {
  if (cdp.ws?.readyState === WebSocket.OPEN) { try { await cdp.send('Browser.close') } catch {} cdp.ws.close() } else browser.kill()
}
