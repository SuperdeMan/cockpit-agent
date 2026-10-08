// Isolated visual QA browser: Vite only, gateway frames remain in browser memory.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { resolve, join } from 'node:path'
import assert from 'node:assert/strict'
import { Cdp, sleep } from './driver.mjs'

export const root = resolve(import.meta.dirname, '../..')
export const base = process.env.HMI_VISUAL_URL || 'http://127.0.0.1:5188'
export async function withVisualPage(batch, run) {
  const out = join(root, `.artifacts/hmi-visual-v2/${batch}`)
  mkdirSync(out, { recursive: true })
  const exe = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(existsSync)
  assert.ok(exe, 'Edge is required')
  const port = 9339
  const browser = spawn(exe, ['--headless=new', '--no-first-run', '--force-device-scale-factor=1',
    `--remote-debugging-port=${port}`, `--user-data-dir=${join(out, 'browser-profile')}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
  const cdp = new Cdp()
  try {
    let pages
    for (let i = 0; i < 60; i++) {
      try { pages = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (pages.length) break } catch {}
      await sleep(250)
    }
    assert.ok(pages?.length, 'browser did not start')
    cdp.ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl)
    await new Promise((res, rej) => { cdp.ws.onopen = res; cdp.ws.onerror = rej })
    cdp.ws.onmessage = ev => cdp._onMessage(String(ev.data))
    for (const domain of ['Page', 'Runtime', 'Network']) await cdp.send(`${domain}.enable`)
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false })
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `
      const NativeSocket=window.WebSocket;
      window.__visualSent=[];
      window.WebSocket=class {
        static OPEN=1; static CLOSED=3;
        constructor(url,...options){
          if(!String(url).includes('localhost:8090/ws')) return new NativeSocket(url,...options);
          this.readyState=0; window.__visualSocket=this;
          setTimeout(()=>{this.readyState=1;this.onopen?.({})},10);
        }
        send(data){window.__visualSent.push(JSON.parse(data))}
        close(){this.readyState=3}
      };
      localStorage.setItem('cockpit.settings.v1',JSON.stringify({ttsEnabled:false,autoplay:false}));
    ` })
    const go = async query => {
      await cdp.send('Page.navigate', { url: base + '/' + query })
      await cdp.waitFor("!!document.querySelector('.au-panel') && window.__visualSocket?.readyState===1")
      await cdp.eval('document.fonts.ready.then(()=>true)')
      await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
      await sleep(600)
    }
    const screenshot = async name => {
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' })
      writeFileSync(join(out, name + '.png'), Buffer.from(data, 'base64'))
    }
    await run({ cdp, out, go, screenshot })
  } finally {
    if (cdp.ws?.readyState === WebSocket.OPEN) { try { await cdp.send('Browser.close') } catch {} cdp.ws.close() }
    else browser.kill()
  }
}
