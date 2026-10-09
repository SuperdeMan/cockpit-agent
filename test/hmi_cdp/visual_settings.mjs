// Visual v2 I6: isolated fixtures, local preference wiring and keyboard access.
import assert from 'node:assert/strict'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { withVisualPage } from './visual_browser.mjs'
import { sleep } from './driver.mjs'

await withVisualPage('i6', async ({cdp,out,go,screenshot}) => {
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', {source: `
    localStorage.removeItem('cockpit.visual.developer'); localStorage.removeItem('cockpit.visual.diagnostics');
    const nativeFetch=window.fetch;window.__settingsRequests=[];
    const voices=['冰糖雪梨','清风徐来','晨间电台','温暖陪伴','少年心气','元气满满'].map((name,i)=>({voice_id:i?'fixture-'+i:'longxiaochun_v3',name,language:'zh',gender:'female',description:'视觉夹具 · 普通话'}));
    const llm={providers:[{id:'fixture',label:'视觉样本',available:true,primary:'fixture-raw-model',models:[{id:'fixture-raw-model',label:'标准'}]}],active:{provider:'fixture',model:'fixture-raw-model'},health:{fixture:{window:4,err:0,ewma_latency_ms:820,last_error:'fixture-raw-error'}}};
    window.fetch=async(input,options={})=>{
      const url=String(input);if(!url.includes('localhost:50059')&&!url.includes('localhost:8090'))return nativeFetch(input,options);
      window.__settingsRequests.push({url,method:options.method||'GET',body:options.body});
      if(new URLSearchParams(location.search).has('read-failure'))return new Response('{}',{status:503,headers:{'Content-Type':'application/json'}});
      let data={ok:true};
      if(url.includes('/api/tts/stream/info'))data={providers:[{id:'cosyvoice',label:'流式',available:true,streaming:true,model:'fixture-tts-model',sample_rate:24000,voices},{id:'mimo',label:'经典',available:true,streaming:false,model:'fixture-tts-classic',voices}]};
      else if(url.includes('/api/llm/providers'))data=llm;
      else if(url.includes('/api/memory/profile'))data={preferences:[{id:'pref-1',text:'喜欢空调 24 度',provenance:'user_stated'},{id:'managed-1',text:'叫我小陈',managed:true}],places:[{id:'place-1',key:'company',name:'文化广场 B 座'}],episodes:[{text:'周末常去露营'}]};
      else if(url.includes('/api/memory/session'))data={turns:[{role:'user',text:'我喜欢清淡一点'}]};
      else if(url.includes('/api/voiceprint/info'))data={enabled:true,provider:'fixture',occupants:[{occupant_id:'primary',display_name:'乘员样本',sample_count:3,stale:false},{occupant_id:'occ-fixture',display_name:'另一位乘员',sample_count:3,stale:true}]};
      return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
    };
  `})
  const click = async (selector) => { await cdp.eval(`document.querySelector(${JSON.stringify(selector)}).click()`); await sleep(80) }
  const report=[]
  for(const theme of ['dark','light']) {
    await go(`?settings=tts&theme=${theme}`)
    await cdp.waitFor("document.querySelectorAll('.au-settings-nav-item').length===12")
    await click('.au-setting-row .au-toggle')
    for(let i=0;i<12;i++) {
      await click(`.au-settings-nav-item:nth-child(${i+1})`)
      const size=await cdp.eval(`(()=>{const p=document.querySelector('.au-settings-content');return {section:p.dataset.section,width:p.getBoundingClientRect().width,overflow:p.scrollWidth>p.clientWidth+1,rows:[...p.querySelectorAll('.au-setting-row')].map(x=>x.getBoundingClientRect().height),unlabelled:[...p.querySelectorAll('[role=switch]')].filter(x=>!x.getAttribute('aria-labelledby')).length}})()`)
      assert.ok(size.width<=1120,size.section+' width')
      assert.equal(size.overflow,false,size.section+' overflow')
      assert.ok(size.rows.every(h=>h>=80),size.section+' row height')
      assert.equal(size.unlabelled,0,size.section+' switch label')
      if(size.section==='occupants') assert.ok(await cdp.eval("[...document.querySelectorAll('.au-setting-button.danger')].every(x=>x.getBoundingClientRect().height<=parseFloat(getComputedStyle(x).getPropertyValue('--au-control-target'))+2)"),'short delete labels stay on one line')
      if(size.section==='developer') {
        assert.equal(await cdp.eval("!!document.querySelector('.au-developer-log')"),false)
        await click('.au-setting-row .au-toggle')
      }
      await screenshot(`${theme}-${size.section}`)
      report.push({theme,...size})
    }
    assert.equal(await cdp.eval("window.__settingsRequests.filter(x=>x.method!=='GET').length"),0,'visual read did not write backend')
  }
  await go('?settings=tts&theme=dark')
  const badWidth = await cdp.eval("(()=>{const el=document.querySelector('.au-settings-content');el.style.maxWidth='1500px';const width=el.getBoundingClientRect().width;el.style.maxWidth='';return width})()")
  assert.throws(()=>assert.ok(badWidth<=1120),'negative control: the layout scan rejects an oversized content column')
  await click('.au-setting-row .au-toggle')
  assert.equal(await cdp.eval("JSON.parse(localStorage.getItem('cockpit.settings.v1')).ttsEnabled"),true,'row toggles once')
  await click('.au-voice-tile:nth-child(2) .au-voice-choice')
  assert.equal(await cdp.eval("JSON.parse(localStorage.getItem('cockpit.settings.v1')).voiceId"),'fixture-1')
  assert.equal(await cdp.eval("document.querySelector('.au-settings-content').innerText.includes('fixture-tts-model')"),false)
  await click('.au-settings-nav-item:nth-child(2)')
  await click('.au-setting-group:last-of-type .au-select-trigger')
  assert.equal(await cdp.eval('document.activeElement.textContent'),'15s','open focuses the selected option')
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'End',code:'End'})
  assert.equal(await cdp.eval('document.activeElement.textContent'),'60s','End focuses last option')
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13})
  await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13})
  await sleep(80)
  assert.equal(await cdp.eval("JSON.parse(localStorage.getItem('cockpit.settings.v1')).listenSeconds"),60,'keyboard select preserves numeric value')
  await click('.au-settings-nav-item:nth-child(7)')
  await cdp.eval("[...document.querySelectorAll('.au-segmented button')].find(x=>x.textContent==='大字').click()")
  await sleep(80)
  assert.equal(await cdp.eval('document.documentElement.dataset.font'),'large')
  assert.equal(await cdp.eval("parseFloat(getComputedStyle(document.querySelector('.au-setting-label')).fontSize)"),28)
  await screenshot('large-display')
  await click('.au-settings-nav-item:nth-child(1)')
  assert.equal(await cdp.eval("[...document.querySelectorAll('.au-voice-name')].every(x=>x.scrollWidth<=x.clientWidth+1)"),true,'four-character names fit')
  await screenshot('large-voice-tiles')
  await cdp.eval("document.querySelector('[aria-label=\"关闭设置\"]').focus()")
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',modifiers:8})
  assert.equal(await cdp.eval("document.querySelector('.au-settings-overlay').contains(document.activeElement)"),true,'focus stays in dialog')
  await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape'})
  assert.equal(await cdp.eval("!!document.querySelector('.au-settings-overlay')"),false,'Escape closes settings')
  await go('?settings=memory&theme=dark')
  const writes = () => cdp.eval("window.__settingsRequests.filter(x=>x.method!=='GET')")
  await click('.au-memory-item [aria-label^="删除：喜欢"]')
  assert.equal((await writes()).length,0,'no delete before confirmation')
  await screenshot('memory-delete-confirmation')
  await click('.au-confirm-actions button:first-child')
  assert.equal((await writes()).length,0,'cancel does not delete')
  await click('.au-memory-item [aria-label^="删除：喜欢"]')
  await click('.au-confirm-actions button:last-child')
  const itemWrites=await writes()
  assert.equal(itemWrites.length,1)
  assert.equal(itemWrites[0].method,'DELETE')
  assert.ok(itemWrites[0].url.includes('/api/memory/items/pref-1?'))
  assert.equal(await cdp.eval("[...document.querySelectorAll('.au-memory-item')].find(x=>x.textContent.includes('周末')).querySelector('button')===null"),true,'episode is not a false per-item deletion')
  await cdp.eval("[...document.querySelectorAll('.au-setting-button')].find(x=>x.textContent==='清除全部经历').click()")
  assert.equal((await writes()).length,1,'scope delete waits for confirmation')
  assert.ok(await cdp.eval("document.querySelector('.au-confirm-dialog').innerText.includes('其他乘员')"))
  await click('.au-confirm-actions button:last-child')
  assert.deepEqual(JSON.parse((await writes()).at(-1).body),{user_id:'u1',scope:'episodic.general'})
  await cdp.eval("[...document.querySelectorAll('.au-setting-button')].find(x=>x.textContent==='清除全部').click()")
  assert.ok(await cdp.eval("document.querySelector('.au-confirm-dialog').innerText.includes('关联声纹身份')"))
  await click('.au-confirm-actions button:last-child')
  assert.deepEqual(JSON.parse((await writes()).at(-1).body),{user_id:'u1',scope:''})
  await go('?settings=tts&read-failure&theme=light')
  assert.ok(await cdp.eval("document.querySelector('.au-settings-read-state').innerText.includes('已有选项')"))
  await screenshot('read-failure')
  await go('?settings=developer&demo=states&dev')
  await click('.au-setting-row:nth-of-type(3) .au-toggle')
  assert.ok(await cdp.eval("document.querySelector('.au-developer-log').innerText.includes('已重试 2 次')"))
  await screenshot('developer-raw-errors')
  await click('[aria-label="关闭设置"]')
  assert.ok(await cdp.eval("document.querySelector('.au-error .au-answer').innerText.includes('已重试 2 次')"))
  await go('?demo=results&settings=display&theme=light')
  await cdp.eval("[...document.querySelectorAll('.au-segmented button')].find(x=>x.textContent==='大字').click()")
  await cdp.eval("[...document.querySelectorAll('.au-setting-row')].find(x=>x.querySelector('.au-setting-label')?.textContent==='行车模式').querySelector('.au-toggle').click()")
  await sleep(80)
  assert.equal(await cdp.eval('document.documentElement.dataset.drive'),'on')
  await screenshot('large-driving-settings')
  await click('[aria-label="关闭设置"]')
  const accessibility=await cdp.eval(`({width:document.body.scrollWidth,orbs:document.querySelectorAll('.au-orb').length,
    input:!!document.querySelector('.au-input'),target:parseFloat(getComputedStyle(document.querySelector('.au-icon-btn')).height),scrim:getComputedStyle(document.querySelector('.au-stage-scrim')).display,
    reduced:[...document.querySelectorAll('.au-orb *')].every(x=>getComputedStyle(x).animationName==='none')})`)
  assert.equal(accessibility.width,1920); assert.equal(accessibility.orbs,1); assert.equal(accessibility.input,false); assert.ok(accessibility.target>=84); assert.ok(accessibility.reduced)
  assert.equal(accessibility.scrim,'none','driving stage text is not obscured by the parked scrim')
  await screenshot('large-driving-answer')
  writeFileSync(join(out,'browser-evidence.json'),JSON.stringify({sections:report,keyboardSelect:true,localVoiceSelection:true,largeFont:true,focusTrap:true,deletionConfirmation:true,exactItemAndScope:true,readFailure:true,rawErrorsOptIn:true,accessibility,backend:'all HTTP/WS intercepted in browser; no real business writes'},null,2))
  console.log('PASS: 24 themed settings sections, keyboard/large-font/focus checks, exact deletion confirmation, read fallback and opt-in diagnostics; mock backend only')
})
