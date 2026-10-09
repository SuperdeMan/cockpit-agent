import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'
import { withVisualPage } from './visual_browser.mjs'
import { cardFixtures } from '../../hmi/src/cardFixtures.mjs'

await withVisualPage('i4', async ({cdp,out,go}) => {
  const fixtures = cardFixtures(), evidence=[], failures=[]
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1100,height:1080,deviceScaleFactor:1,mobile:false})
  for (const theme of ['dark','light']) for (const [i,fixture] of fixtures.entries()) {
    try {
      await go('?card-gallery='+i+'&theme='+theme)
      const metrics=await cdp.eval(`(() => {
        const root=document.querySelector('.au-gallery-preview');
        return {type:root.querySelector('[data-card-type]')?.getAttribute('data-card-type'),text:root.innerText,
          width:root.clientWidth,scroll:root.scrollWidth,height:root.scrollHeight,
          overflow:[...root.querySelectorAll('*')].filter(n=>n.clientWidth>0&&n.scrollWidth>n.clientWidth+3&&!['auto','scroll','hidden'].includes(getComputedStyle(n).overflowX)&&n.namespaceURI!=='http://www.w3.org/2000/svg').slice(0,8).map(n=>({tag:n.tagName,cls:n.className,client:n.clientWidth,scroll:n.scrollWidth})),
          qr:root.querySelector('img[alt="付款码"]')?getComputedStyle(root.querySelector('img[alt="付款码"]')).backgroundColor:null};
      })()`)
      assert.ok(metrics.text.trim(),'empty card')
      assert.ok(metrics.scroll<=metrics.width+3,'card overflow: '+JSON.stringify(metrics.overflow))
      if(metrics.qr) assert.equal(metrics.qr,'rgb(255, 255, 255)')
      const height=Math.min(8000,Math.max(1080,metrics.height+180))
      const shot=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1100,height,scale:1}})
      const file=theme+'-'+String(i).padStart(2,'0')+'.png'
      writeFileSync(join(out,file),Buffer.from(shot.data,'base64'))
      evidence.push({theme,index:i,type:fixture.card.type,file,...metrics})
    } catch(error) { failures.push({theme,index:i,type:fixture.card.type,error:String(error)}) }
  }
  for(const i of [0,2,13,16,27,37]) {
    await go('?card-gallery='+i+'&drive=on')
    const summary=await cdp.eval(`({fields:document.querySelectorAll('.au-driving-field').length,qr:document.querySelectorAll('img[alt="付款码"]').length,text:document.querySelector('.au-driving-card')?.textContent})`)
    assert.ok(summary.fields<=2)
    assert.equal(summary.qr,0)
    assert.ok(summary.text)
  }
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false})
  for(const demo of ['cards','info','results']) {
    await go('?demo='+demo)
    const shot=await cdp.send('Page.captureScreenshot',{format:'png'})
    writeFileSync(join(out,'demo-'+demo+'.png'),Buffer.from(shot.data,'base64'))
  }
  writeFileSync(join(out,'browser-evidence.json'),JSON.stringify({cases:evidence.length,types:[...new Set(fixtures.map(f=>f.card.type))],failures,evidence},null,2))
  console.log(JSON.stringify({cases:evidence.length,types:new Set(fixtures.map(f=>f.card.type)).size,failures}))
  assert.deepEqual(failures,[])
})
