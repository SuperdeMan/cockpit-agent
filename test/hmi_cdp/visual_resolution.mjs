// Foreground balance and map framing across desktop/cockpit viewports.
import assert from 'node:assert/strict'
import {writeFileSync} from 'node:fs'
import {join} from 'node:path'
import {withVisualPage} from './visual_browser.mjs'
import {sleep} from './driver.mjs'

const displays=process.env.HMI_RESOLUTION_SMOKE==='1' ? [[3440,1440,1]] : [
  [1920,1080,1],[1920,720,1],[1920,1200,1],[2560,1440,1],
  [3440,1440,1],[3840,2160,1],[5120,1440,1],[2752,1152,1.25],[2293,960,1.5],
]
const variants=process.env.HMI_RESOLUTION_SMOKE==='1' ? [['dark','normal']] : [
  ['dark','normal'],['light','normal'],['dark','large'],['light','large'],
]
await withVisualPage('resolution',async({cdp,out,go,screenshot})=>{
  const rows=[],failures=[]
  await cdp.send('Page.addScriptToEvaluateOnNewDocument',{source:`
    const resolutionSettings=JSON.parse(localStorage.getItem('cockpit.settings.v1')||'{}');
    resolutionSettings.fontScale=new URLSearchParams(location.search).get('qa-font')==='large'?'large':'normal';
    localStorage.setItem('cockpit.settings.v1',JSON.stringify(resolutionSettings));
  `})
  const rects=()=>cdp.eval(`(()=>{
    const rect=s=>document.querySelector(s)?.getBoundingClientRect().toJSON();
    return {viewport:[innerWidth,innerHeight],bodyWidth:document.body.scrollWidth,
      main:rect('.au-main'),panel:rect('.au-panel'),stage:rect('.au-stage'),content:rect('.au-stage-content'),
      dial:rect('.au-battery-dial'),metrics:rect('.au-idle-metrics'),tools:rect('.au-map-topbar'),
      settings:rect('.au-settings-body'),settingsContent:rect('.au-settings-content'),
      orbs:document.querySelectorAll('.au-orb').length,input:!!document.querySelector('.au-input'),
      driving:document.documentElement.dataset.drive==='on',sent:window.__visualSent.length,
      markers:[...document.querySelectorAll('.au-map-marker')].map(e=>e.getBoundingClientRect().toJSON())};
  })()`)
  const check=(value,name,message)=>{if(!value)failures.push({name,message})}
  function inspect(r,name,kind) {
    const [width,height]=r.viewport
    check(r.bodyWidth===width,name,'page overflows viewport horizontally')
    check(r.panel.left>=0&&r.panel.right<=width&&r.panel.top>=0&&r.panel.bottom<=height+1,name,'conversation leaves viewport')
    check(r.orbs===1&&r.sent===0,name,'single orb / zero business requests')
    check(r.stage.width===width&&r.stage.height===height,name,'background must fill the viewport')
    if(width<=1920)check(r.main.left===24,name,'baseline conversation position changed')
    if(kind==='settings') {
      check(r.settings.width<=1920&&Math.abs(r.settings.left+r.settings.width/2-width/2)<1,name,'settings workspace is not centered and bounded')
      check(r.settingsContent.width<=1120,name,'settings reading width expanded')
    } else if(width>2240&&!r.driving) {
      const right=kind==='map'?r.tools.right:r.content.right
      check(right-r.main.left<=2240&&Math.abs((right+r.main.left)/2-width/2)<=16,name,'foreground spreads across the ultrawide viewport')
    }
    if(kind==='idle'&&width>1920)check(Math.abs((r.dial.left+r.metrics.right)/2-(r.content.left+r.content.width/2))<2,name,'idle summary is not centered in its stage area')
    if(kind==='map') {
      check(r.markers.length>=2,name,'map markers missing')
      for(const m of r.markers) {
        check(m.left>= (r.driving?r.tools.left:r.panel.right)-2,name,'map marker lies behind conversation / outer gutter')
        check(m.right<=r.tools.right+2,name,'map marker leaves the foreground right boundary')
      }
      check(r.input===!r.driving,name,'driving input visibility changed')
    }
  }
  for(const [width,height,dpr] of displays)for(const [theme,font] of variants) {
    await cdp.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:dpr,mobile:false})
    for(const [kind,query] of [['idle',''],['conversation','demo=results'],['settings','settings=display'],['map','demo=map-sdk']]) {
      const name=`${width}-${height}-${dpr}-${theme}-${font}-${kind}`
      await go(`?${query}&theme=${theme}&qa-font=${font}`)
      if(kind==='map'){await cdp.waitFor("document.querySelector('.au-map-stage')?.dataset.mapState==='ready'",45000);await sleep(600)}
      const r=await rects();inspect(r,name,kind);rows.push({name,kind,...r})
      if(width===3440||width===1920&&height===1080&&theme==='dark'&&font==='normal')await screenshot(name)
      if(kind==='map') {
        await cdp.eval("document.querySelector('[aria-label=打开设置]').click();true")
        await cdp.waitFor("!!document.querySelector('.au-settings-overlay')")
        await cdp.eval("[...document.querySelectorAll('.au-settings-nav-item')].find(e=>e.textContent.trim()==='显示').click();true")
        await cdp.eval("[...document.querySelectorAll('.au-setting-row')].find(e=>e.querySelector('.au-setting-label')?.textContent==='行车模式').querySelector('.au-toggle').click();document.querySelector('[aria-label=关闭设置]').click();true")
        await cdp.waitFor("document.documentElement.dataset.drive==='on'");await sleep(700)
        const driving=await rects();inspect(driving,name+'-driving','map');rows.push({name:name+'-driving',kind:'map',...driving})
        if(width===3440)await screenshot(name+'-driving')
      }
      writeFileSync(join(out,'resolution-evidence.json'),JSON.stringify({rows,failures},null,2))
    }
    console.log(JSON.stringify({width,height,dpr,theme,font,cases:rows.length,failures:failures.length}))
  }
  // The layout scan must reject restoring the original left-pinned foreground.
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:3440,height:1440,deviceScaleFactor:1,mobile:false})
  await go('')
  const negative=await cdp.eval(`(()=>{const main=document.querySelector('.au-main');main.style.left='0px';const fails=document.querySelector('.au-stage-content').getBoundingClientRect().right-main.getBoundingClientRect().left>2240;main.style.removeProperty('left');return fails})()`)
  assert.equal(negative,true,'negative control did not detect the regression')
  writeFileSync(join(out,'resolution-evidence.json'),JSON.stringify({rows,failures,negativeControl:true},null,2))
  assert.deepEqual(failures,[])
  console.log(JSON.stringify({passed:true,cases:rows.length,displays:displays.length,negativeControl:true}))
})
