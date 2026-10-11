// Start startup_servers.mjs first; this probe sends no business requests.
import assert from 'node:assert/strict'
import {writeFileSync} from 'node:fs'
import {join} from 'node:path'
import {withVisualPage} from './visual_browser.mjs'
import {sleep} from './driver.mjs'

await withVisualPage('cold-load-benchmark',async({cdp,out,screenshot})=>{
  let requests=new Map(),errors=[]
  const original=cdp.ws.onmessage
  cdp.ws.onmessage=e=>{
    const m=JSON.parse(String(e.data));original(e)
    if(m.method==='Network.responseReceived') {
      const r=m.params.response
      requests.set(m.params.requestId,{path:r.url.split('?')[0],type:m.params.type,status:r.status,encoding:r.headers['Content-Encoding']||r.headers['content-encoding']||'',bytes:0})
    }
    if(m.method==='Network.loadingFinished'&&requests.has(m.params.requestId))requests.get(m.params.requestId).bytes=m.params.encodedDataLength
    if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text)
  }
  await cdp.send('Network.setCacheDisabled',{cacheDisabled:true})
  const network={offline:false,latency:150,downloadThroughput:256*1024,uploadThroughput:128*1024}
  await cdp.send('Network.emulateNetworkConditions',network)
  const rows=[]
  for(let repeat=1;repeat<=3;repeat++)for(const [mode,url] of [['dev','http://127.0.0.1:5194'],['built','http://127.0.0.1:5195']]) {
    requests=new Map();errors=[]
    await cdp.send('Page.navigate',{url:url+'/?cold-benchmark='+repeat})
    await cdp.waitFor("!!document.querySelector('.au-panel')",180000,mode+' UI ready')
    const ready=await cdp.eval('performance.now()')
    await cdp.eval('document.fonts.ready.then(()=>true)')
    await cdp.waitFor("document.readyState==='complete'",180000)
    await sleep(200)
    const browser=await cdp.eval("({navigation:performance.getEntriesByType('navigation').map(r=>({dcl:r.domContentLoadedEventEnd,load:r.loadEventEnd}))[0],isolated:crossOriginIsolated,sharedBuffer:typeof SharedArrayBuffer,orbs:document.querySelectorAll('.au-orb').length,sent:window.__visualSent.length})")
    const resources=[...requests.values()]
    const row={mode,repeat,readyMs:Math.round(ready),...browser,requestCount:resources.length,transferredBytes:resources.reduce((n,r)=>n+r.bytes,0),resources,errors}
    rows.push(row)
    writeFileSync(join(out,'performance.json'),JSON.stringify({network,rows},null,2))
    console.log(JSON.stringify({mode,repeat,readyMs:row.readyMs,requestCount:row.requestCount,bytes:row.transferredBytes,isolated:row.isolated}))
    assert.deepEqual(errors,[])
    assert.equal(browser.orbs,1);assert.equal(browser.sent,0)
    assert.equal(browser.isolated,true);assert.equal(browser.sharedBuffer,'function')
    if(mode==='built') {
      assert.ok(resources.some(r=>r.type==='Script'&&r.encoding==='gzip'))
      assert.ok(!resources.some(r=>/\/@vite\/|\/src\/|\/node_modules\//.test(r.path)))
    }
    if(repeat===1)await screenshot(mode+'-cold')
  }
  const median=list=>[...list].sort((a,b)=>a-b)[Math.floor(list.length/2)]
  const summary=Object.fromEntries(['dev','built'].map(mode=>{const items=rows.filter(r=>r.mode===mode);return [mode,{readyMs:median(items.map(r=>r.readyMs)),bytes:median(items.map(r=>r.transferredBytes)),requests:median(items.map(r=>r.requestCount))}]}))
  writeFileSync(join(out,'summary.json'),JSON.stringify({network,summary},null,2))
  assert.ok(summary.built.bytes<summary.dev.bytes*.2,'transfer reduction below 80%')
  assert.ok(summary.built.requests<summary.dev.requests*.2,'request reduction below 80%')
  assert.ok(summary.built.readyMs<summary.dev.readyMs*.5,'no meaningful load improvement')
  console.log(JSON.stringify({passed:true,summary}))
})
