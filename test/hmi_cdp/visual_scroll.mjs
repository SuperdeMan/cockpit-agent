// Browser regression for B01: process/stream/layout updates must not impersonate a reader.
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { withVisualPage } from './visual_browser.mjs'
import { sleep } from './driver.mjs'
import { cardFixtures } from '../../hmi/src/cardFixtures.mjs'

await withVisualPage('scroll-regression', async ({ cdp, out, go, screenshot }) => {
  const evidence = [], failures = []
  const frame = data => cdp.eval(`window.__visualSocket.onmessage({data:JSON.stringify(${JSON.stringify(data)})});true`)
  const metrics = () => cdp.eval(`(()=>{const c=document.querySelector('.chat');return {
    top:c.scrollTop,height:c.scrollHeight,viewport:c.clientHeight,gap:c.scrollHeight-c.clientHeight-c.scrollTop,
    reading:c.classList.contains('reading-history'),notice:!!document.querySelector('.au-jump-latest')};})()`)
  const verify = (ok, message) => { if (!ok) failures.push(message) }
  const begin = async text => {
    await cdp.typeAndSend(text)
    return await cdp.eval('window.__visualSent.at(-1).request_id')
  }
  const process = async request_id => {
    for (const step of [
      {phase:'understand',summary:'分别查询两个城市的天气'},
      {phase:'plan',summary:'先查询每个城市，再整理对比结果'},
      {phase:'execute',step_id:'weather-a',label:'查询第一个城市',status:'running'},
      {phase:'execute',step_id:'weather-b',label:'查询第二个城市',status:'running'},
      {phase:'execute',step_id:'weather-a',label:'查询第一个城市',status:'done',summary:'查询完成'},
      {phase:'synthesize',summary:'正在整理两地结果'},
    ]) { await frame({type:'process',request_id,...step}); await sleep(70) }
  }
  const finish = async request_id => {
    for (const delta of ['第一座城市晴。','第二座城市多云。','以下是查询结果。']) {
      await frame({type:'speech_delta',request_id,delta});await sleep(70)
    }
    await frame({type:'final',request_id,speech:'第一座城市晴，第二座城市多云。',ui_card:cardFixtures()[0].card})
    await sleep(180)
  }
  for (const height of [1080,720]) for (const theme of ['dark','light']) {
    const name = `${theme}-${height}`
    await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height,deviceScaleFactor:1,mobile:false})
    await go(`?demo=states&theme=${theme}`)
    // The baseline starts at the tail; there are no wheel/key/scrollbar inputs during this turn.
    const before = await metrics()
    assert.ok(before.gap < 2, 'tail precondition')
    const request = await begin('分别查询两座城市今天的天气')
    await process(request);await finish(request)
    const after = await metrics()
    verify(after.gap < 2 && !after.notice && !after.reading, name+' stopped following process/final updates')
    await screenshot(name+'-tail')
    evidence.push({name,kind:'tail',before,after})
    writeFileSync(join(out,'scroll-evidence.json'),JSON.stringify({evidence,failures},null,2))

    // Deliberate wheel input while a reply is in flight must still preserve history (D12).
    await cdp.eval("document.querySelector('.au-jump-latest')?.click();true")
    const second = await begin('继续查询两座城市')
    await sleep(120)
    const point=await cdp.eval("(()=>{const r=document.querySelector('.chat').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
    await cdp.send('Input.dispatchMouseEvent',{type:'mouseWheel',...point,deltaX:0,deltaY:-700})
    await cdp.waitFor("document.querySelector('.chat').classList.contains('reading-history')",2000)
    const reading = await metrics()
    await process(second);await finish(second)
    const retained = await metrics()
    verify(Math.abs(reading.top-retained.top)<2 && retained.notice, name+' moved a history reader')
    await screenshot(name+'-history')
    await cdp.eval("document.querySelector('.au-jump-latest').click();true")
    await sleep(100)
    const jumped = await metrics()
    verify(jumped.gap<2 && !jumped.notice, name+' failed jump to latest')
    evidence.push({name,kind:'history',before:reading,after:retained,jumped})
  }
  // A late image/card measurement and viewport resize may happen without a new message.
  await go('?demo=states')
  const resizes=[]
  for (const height of [1080,720,1080]) {
    await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height,deviceScaleFactor:1,mobile:false})
    await cdp.eval("document.querySelector('.au-chat-content').style.paddingBottom='220px';true")
    await sleep(160)
    const value=await metrics();resizes.push({viewportHeight:height,...value})
    verify(value.gap<2&&!value.notice,'resize detached the tail at '+height)
  }
  await cdp.eval("document.querySelector('.chat').scrollTop=0;true")
  await cdp.waitFor("document.querySelector('.chat').classList.contains('reading-history')",2000)
  const reader=await metrics()
  await cdp.eval("document.querySelector('.au-chat-content').style.paddingBottom='400px';true")
  await sleep(160)
  verify((await metrics()).top===reader.top,'late content resize moved the history reader')
  writeFileSync(join(out,'scroll-evidence.json'),JSON.stringify({evidence,resizes,failures},null,2))
  console.log(JSON.stringify({cases:evidence.length,resizes:resizes.length,failures}))
  assert.deepEqual(failures,[])
})
