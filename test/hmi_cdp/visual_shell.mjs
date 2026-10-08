import { writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { join } from 'node:path'
import assert from 'node:assert/strict'
import { withVisualPage, root } from './visual_browser.mjs'

await withVisualPage('i2', async ({ cdp, out, go, screenshot }) => {
  const evidence = []
  for (const theme of ['dark', 'light']) for (const demo of ['', 'demo', 'demo=map', 'demo=states']) {
    await go(`?${demo}&theme=${theme}`)
    const geometry = await cdp.eval(`(() => {
      const panel=document.querySelector('.au-panel'), input=document.querySelector('.au-input');
      return {panel:panel.getBoundingClientRect().toJSON(),stage:document.querySelector('.au-stage').getBoundingClientRect().toJSON(),
        orbs:document.querySelectorAll('.au-orb').length, chips:document.querySelectorAll('.au-quick-chip').length,
        input:input.getBoundingClientRect().toJSON(),bodyWidth:document.body.scrollWidth,
        reduced:[...document.querySelectorAll('.au-orb *')].every(n=>getComputedStyle(n).animationName==='none'),
        button:getComputedStyle(document.querySelector('.au-send')).backgroundImage};
    })()`)
    assert.equal(geometry.orbs, 1)
    assert.equal(geometry.panel.width, 800)
    assert.equal(geometry.panel.x, 24)
    assert.equal(geometry.stage.width, 1920)
    assert.equal(geometry.bodyWidth, 1920)
    assert.ok(geometry.chips <= 4)
    assert.ok(geometry.reduced)
    assert.equal(geometry.button, 'none')
    const name = `${theme}-${demo.replace('=', '-') || 'welcome'}`
    await screenshot(name)
    evidence.push({name,geometry})
  }
  await go('?settings=display')
  await cdp.eval("[...document.querySelectorAll('[role=switch]')].at(-1).click(); true")
  await cdp.waitFor("document.documentElement.dataset.drive==='on'")
  await cdp.eval("document.querySelector('[aria-label=关闭设置]')?.click(); true")
  await cdp.waitFor("!document.querySelector('.au-settings-overlay')")
  const driving = await cdp.eval("({inputs:document.querySelectorAll('.au-input').length,chips:document.querySelectorAll('.au-quick-chip').length,answer:!!document.querySelector('.au-driving-answer')})")
  assert.deepEqual(driving, { inputs: 0, chips: 0, answer: true })
  await screenshot('driving')
  // The actual send and stop controls still use the existing App paths.
  await go('')
  await cdp.typeAndSend('讲个笑话')
  await cdp.waitFor("document.querySelector('.au-send')?.getAttribute('aria-label')==='停止'")
  await cdp.eval("document.querySelector('.au-send').click(); true")
  const frames = await cdp.eval('window.__visualSent')
  assert.ok(frames.some(f => f.text === '讲个笑话' && !f.is_confirmation))
  assert.ok(frames.some(f => f.type === 'cancel'))
  await go('?settings=display')
  await cdp.eval("document.querySelector('[aria-label=关闭设置]').click(); true")
  await cdp.typeAndSend('讲个笑话')
  const requestId = await cdp.eval('window.__visualSent.at(-1).request_id')
  await cdp.eval(`window.__visualSocket.onmessage({data:JSON.stringify({type:'final',request_id:${JSON.stringify(requestId)},
    speech:'已准备好简短回答。',result_bundles:[{version:1,task_id:'visual',revision:1,goals:[],results:[],display_text:'完整答案还有许多段落。'}]})}); true`)
  await cdp.eval("document.querySelector('[aria-label=打开设置]').click(); true")
  await cdp.waitFor("!!document.querySelector('.au-settings-overlay')")
  await cdp.eval("[...document.querySelectorAll('[role=switch]')].at(-1).click(); true")
  await cdp.eval("document.querySelector('[aria-label=关闭设置]').click(); true")
  await cdp.waitFor("document.querySelector('.au-driving-answer')?.dataset.source==='speech'")
  assert.equal(await cdp.eval("document.querySelector('.au-driving-answer').textContent"), '已准备好简短回答。')
  await screenshot('driving-short-speech')
  await go('?demo&theme=light')
  const fallback = await cdp.eval(`(() => {
    const rules=[...document.styleSheets].flatMap(sheet=>[...sheet.cssRules])
      .filter(rule=>rule.constructor.name==='CSSSupportsRule' && rule.conditionText.startsWith('not') && rule.conditionText.includes('backdrop-filter'));
    if(!rules.length) throw new Error('no no-blur fallback');
    const style=document.createElement('style'); style.textContent=rules.flatMap(rule=>[...rule.cssRules].map(r=>r.cssText)).join('\\n'); document.head.append(style);
    return {rules:rules.length,panel:getComputedStyle(document.querySelector('.au-panel')).backgroundColor};
  })()`)
  assert.equal(fallback.panel, 'rgb(255, 255, 255)')
  await screenshot('light-fallback-rule')
  writeFileSync(join(out, 'browser-evidence.json'), JSON.stringify({baseSha:execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim(),dirty:true,evidence,driving,sendAndStop:true,shortSpeech:true,fallback},null,2))
  console.log('PASS: 8 shell fixtures, single orb, 800 px panel, full stage, reduced motion, driving input gating, send/cancel wiring')
})
