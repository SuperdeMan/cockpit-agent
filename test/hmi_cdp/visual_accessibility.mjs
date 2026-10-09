// Follow-up visual stress: large typography at the real 800 px panel's inner width.
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { withVisualPage } from './visual_browser.mjs'
import { cardFixtures } from '../../hmi/src/cardFixtures.mjs'
import { sleep } from './driver.mjs'

await withVisualPage('followup-visual', async ({cdp,out,go,screenshot}) => {
  const failures=[], cards=[], layouts=[]
  await go('')
  assert.equal(await cdp.eval("!!document.querySelector('.au-demo-badge')"),false,'normal app is not labelled as a fixture')
  await go('?demo=cards')
  assert.equal(await cdp.eval("document.querySelector('.au-demo-badge')?.textContent"),'含示例数据')
  const innerWidth=await cdp.eval("document.querySelector('.au-conv-panel').clientWidth")
  assert.ok(innerWidth>650 && innerWidth<800)
  for(const theme of ['dark','light']) for(const [index,fixture] of cardFixtures().entries()) {
    await go(`?card-gallery=${index}&font=large&theme=${theme}`)
    await cdp.eval(`document.querySelector('.au-gallery-preview').style.width='${innerWidth}px'`)
    const metrics=await cdp.eval(`(()=>{const root=document.querySelector('.au-gallery-preview');return {width:root.clientWidth,scroll:root.scrollWidth,text:root.innerText,
      spills:[...root.querySelectorAll('*')].filter(x=>x.clientWidth>0&&x.scrollWidth>x.clientWidth+3&&!['auto','scroll','hidden'].includes(getComputedStyle(x).overflowX)&&x.namespaceURI!=='http://www.w3.org/2000/svg').slice(0,6).map(x=>({tag:x.tagName,cls:x.className,width:x.clientWidth,scroll:x.scrollWidth}))}})()`)
    if(metrics.scroll>metrics.width+3) {
      failures.push({theme,index,type:fixture.card.type,...metrics})
      await screenshot(`${theme}-${index}-overflow`)
    } else if([0,9,22,31,37].includes(index)) await screenshot(`${theme}-${index}-large`)
    cards.push({theme,index,type:fixture.card.type,width:metrics.width,scroll:metrics.scroll})
    if(cards.length%20===0) console.log(`large cards checked: ${cards.length}, overflow: ${failures.length}`)
  }
  // Negative control: a deliberately oversized child must be rejected by this scan.
  const invalid=await cdp.eval(`(()=>{const root=document.querySelector('.au-gallery-preview'),bad=document.createElement('div');bad.style.width='1500px';bad.style.height='1px';root.append(bad);const result=root.scrollWidth>root.clientWidth+3;bad.remove();return result})()`)
  assert.equal(invalid,true)
  for(const height of [720,1080,1200]) for(const theme of ['dark','light']) for(const driving of [false,true]) {
    await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height,deviceScaleFactor:1,mobile:false})
    await go(`?demo=results&settings=display&theme=${theme}`)
    await cdp.eval("[...document.querySelectorAll('.au-segmented button')].find(x=>x.textContent==='大字').click()")
    if(driving) await cdp.eval("[...document.querySelectorAll('.au-setting-row')].find(x=>x.querySelector('.au-setting-label')?.textContent==='行车模式').querySelector('.au-toggle').click()")
    await cdp.eval("document.querySelector('[aria-label=\"关闭设置\"]').click()")
    await sleep(80)
    const metric=await cdp.eval(`(()=>{const p=document.querySelector('.au-panel').getBoundingClientRect();return {bodyWidth:document.body.scrollWidth,panel:{x:p.x,y:p.y,right:p.right,bottom:p.bottom},input:!!document.querySelector('.au-input'),orbs:document.querySelectorAll('.au-orb').length,answer:document.querySelector('.au-driving-answer')?.innerText}})()`)
    assert.equal(metric.bodyWidth,1920)
    assert.ok(metric.panel.y>=0 && metric.panel.bottom<=height+1)
    assert.equal(metric.orbs,1)
    assert.equal(metric.input,!driving)
    layouts.push({height,theme,driving,...metric})
    await screenshot(`${theme}-${height}-${driving?'driving':'parked'}`)
  }
  writeFileSync(join(out,'accessibility-evidence.json'),JSON.stringify({innerWidth,cards,layouts,failures,negativeControl:true},null,2))
  console.log(JSON.stringify({largeCards:cards.length,heightLayouts:layouts.length,failures:failures.map(({theme,index,type})=>({theme,index,type}))}))
  assert.deepEqual(failures,[])
})
