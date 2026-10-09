import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { withVisualPage } from './visual_browser.mjs'
import { cardFixtures } from '../../hmi/src/cardFixtures.mjs'

await withVisualPage('i5',async({cdp,out,go,screenshot})=>{
 const frame=data=>cdp.eval('window.__visualSocket.onmessage({data:JSON.stringify('+JSON.stringify(data)+')});true')
 const cases=[[0,'weather'],[15,'map'],[20,'map'],[21,'map'],[22,'map'],[19,'map'],[24,'agenda'],[27,'agenda'],[9,'reading'],[31,'reading'],[37,'payment']]
 for(const theme of ['dark','light']) for(const [index,kind] of cases){
   await go('?theme='+theme)
   const card={...cardFixtures()[index].card,_prov:{mode:'mock',vendor:'视觉夹具'}}
   await frame({type:'final',speech:'视觉样例',ui_card:card})
   await cdp.waitFor("document.querySelector('[data-scene]')?.dataset.scene==='"+kind+"'")
   assert.equal(await cdp.eval('document.body.scrollWidth'),1920)
   if(kind==='payment'){
     assert.equal(await cdp.eval("[...document.querySelectorAll('img[alt=付款码]')].filter(e=>e.getBoundingClientRect().width>0).length"),1)
     assert.equal(await cdp.eval("getComputedStyle(document.querySelector('.payment img[alt=付款码]')).backgroundColor"),'rgb(255, 255, 255)')
   }
   await screenshot(theme+'-'+index+'-'+kind)
 }
 await go('')
 assert.match(await cdp.eval("document.querySelector('.au-idle-stage').textContent"),/读不到/)
 await frame({type:'vehicle_state',state:{battery:72,gear:'P'}})
 await cdp.waitFor("document.querySelector('.au-battery-dial').textContent.includes('72')")
 assert.match(await cdp.eval("document.querySelector('.au-idle-metrics').textContent"),/续航读不到/)
 await screenshot('partial-vehicle')
 await frame({type:'vehicle_state',state:{battery:78,range_km:412,gear:'P',aircon:{on:true,temp:24},window:'closed'}})
 await frame({type:'final',speech:'车辆状态样例',actions:[{type:'vehicle.control',payload:{command:'hvac.set',temperature:24}}]})
 await cdp.waitFor("document.querySelector('[data-scene]').dataset.scene==='vehicle'")
 await screenshot('vehicle')
 await frame({type:'final',speech:'媒体样例',actions:[{type:'media.control',payload:{command:'media.play',name:'示例歌曲'}}]})
 await cdp.waitFor("document.querySelector('[data-scene]').dataset.scene==='media'")
 await screenshot('media')
 await go('?settings=display')
 await cdp.eval("document.querySelector('[aria-label=关闭设置]').click();true")
 await frame({type:'final',speech:'视觉样例',ui_card:{...cardFixtures()[37].card,_prov:{mode:'mock',vendor:'视觉夹具'}}})
 await cdp.waitFor("document.querySelector('[data-scene]').dataset.scene==='payment'")
 await cdp.eval("document.querySelector('[aria-label=打开设置]').click();true")
 await cdp.waitFor("!!document.querySelector('.au-settings-overlay')")
 await cdp.eval("[...document.querySelectorAll('[role=switch]')].at(-1).click();document.querySelector('[aria-label=关闭设置]').click();true")
 await cdp.waitFor("document.documentElement.dataset.drive==='on'")
 assert.equal(await cdp.eval("document.querySelectorAll('img[alt=付款码]').length"),0)
 assert.equal(await cdp.eval("document.querySelector('[data-scene]').dataset.scene"),'idle')
 await screenshot('driving-payment-hidden')
 writeFileSync(join(out,'browser-evidence.json'),JSON.stringify({themedStages:cases.length*2,partialRangeNotEstimated:true,vehicle:true,media:true,drivingPaymentHidden:true},null,2))
 console.log('PASS: 22 themed stages, partial vehicle data, vehicle/media surfaces, and driving payment suppression')
})
