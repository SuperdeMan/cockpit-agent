// B02/B03: hit-test real SDK layers against the shell and full-screen settings.
// Requires configured AMap JS credentials in the local Vite server, never in this driver.
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { withVisualPage } from './visual_browser.mjs'
import { sleep } from './driver.mjs'

await withVisualPage('map-layer-regression', async ({ cdp, out, go, screenshot }) => {
  const evidence=[],failures=[]
  const verify=(ok,message)=>{if(!ok)failures.push(message)}
  const click=async selector=>{
    const point=await cdp.eval(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`)
    for(const type of ['mousePressed','mouseReleased'])await cdp.send('Input.dispatchMouseEvent',{type,button:'left',clickCount:1,...point})
    await sleep(100)
  }
  const geometry=()=>cdp.eval(`(()=>{
    const back=document.querySelector('.au-stage-back'),header=document.querySelector('.au-map-header'),bar=document.querySelector('.au-statusbar');
    const rect=e=>e.getBoundingClientRect().toJSON(),r=rect(back);
    const overlaps=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
    return {back:r,header:rect(header),status:rect(bar),
      hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('button')===back,
      titleOverlap:overlaps(r,rect(header)),
      toolOverlap:[...document.querySelectorAll('.au-map-tools button')].filter(e=>e!==back).some(e=>overlaps(r,rect(e)))};
  })()`)
  for(const height of [1080,720])for(const theme of ['dark','light'])for(const font of ['normal','large']) {
    const name=`${theme}-${height}-${font}`
    await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height,deviceScaleFactor:1,mobile:false})
    await go(`?demo=map-sdk&settings=display&theme=${theme}`)
    await cdp.eval(`[...document.querySelectorAll('.au-segmented button')].find(e=>e.textContent===${JSON.stringify(font==='large'?'大字':'标准')}).click();true`)
    await click('[aria-label="关闭设置"]')
    await cdp.waitFor("document.querySelector('.au-map-stage')?.dataset.mapState==='ready'",45000,'real AMap ready')
    await click('[data-card-type="route_plan"] .au-stage-link')
    const layout=await geometry()
    verify(layout.back.top>=layout.status.bottom,name+' return button is under status bar')
    verify(layout.hit&&!layout.titleOverlap&&!layout.toolOverlap,name+' return button is obscured')
    await screenshot(name+'-selected')
    await click('.au-stage-back')
    const returned=await cdp.eval("!document.querySelector('.au-stage-back')")
    verify(returned,name+' return button center does not return to latest')

    // Provider attribution remains present on the map, but must not escape into settings.
    await click('[aria-label="打开设置"]')
    const attribution=await cdp.eval(`(()=>{
      const overlay=document.querySelector('.au-settings-overlay');
      return [...document.querySelectorAll('.amap-logo,.amap-copyright')].map(e=>{
        const r=e.getBoundingClientRect();return {class:e.className,width:r.width,height:r.height,
          coveredBySettings:overlay.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};
      });
    })()`)
    verify(attribution.length===2&&attribution.every(x=>x.width>0&&x.height>0&&x.coveredBySettings),name+' map attribution escapes settings')
    await screenshot(name+'-settings')
    await click('[aria-label="关闭设置"]')
    const visible=await cdp.eval(`(()=>{const e=document.querySelector('.amap-copyright'),r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))})()`)
    verify(visible,name+' map attribution hidden after closing settings')
    evidence.push({name,layout,returned,attribution,attributionRestored:visible})
    writeFileSync(join(out,'map-layer-evidence.json'),JSON.stringify({evidence,failures},null,2))
  }
  assert.equal(await cdp.eval('window.__visualSent.length'),0,'view controls must not send business requests')
  console.log(JSON.stringify({cases:evidence.length,failures}))
  assert.deepEqual(failures,[])
})
