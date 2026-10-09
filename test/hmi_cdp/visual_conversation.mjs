import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'
import { withVisualPage } from './visual_browser.mjs'

await withVisualPage('i3', async ({ cdp, out, go, screenshot }) => {
  const sendFrame = data => cdp.eval('window.__visualSocket.onmessage({data:JSON.stringify(' + JSON.stringify(data) + ')});true')
  for (const theme of ['dark', 'light']) {
    await go('?demo=states&theme=' + theme)
    await screenshot(theme + '-states')
    assert.equal(await cdp.eval("document.querySelectorAll('.au-orb').length"), 1)
    assert.equal(await cdp.eval("document.querySelectorAll('.au-trace').length"), 0)
  }
  // D12: a real scroll event followed by a new response must preserve the reader's position.
  await go('?demo=states')
  await cdp.eval("document.querySelector('.chat').scrollTop=0;true")
  await cdp.waitFor("document.querySelector('.chat').classList.contains('reading-history')")
  const before = await cdp.eval("document.querySelector('.chat').scrollTop")
  await sendFrame({ type: 'final', speech: '这是一条新到的回答，历史阅读位置保持不变。' })
  await cdp.waitFor("!!document.querySelector('.au-jump-latest')")
  assert.equal(await cdp.eval("document.querySelector('.chat').scrollTop"), before)
  await screenshot('history-new-message')
  await cdp.eval("document.querySelector('.au-jump-latest').click();true")
  await cdp.waitFor("(()=>{const e=document.querySelector('.chat');return e.scrollHeight-e.clientHeight-e.scrollTop<48})()")
  // Two authoritative operation ids, a local switch, and only the selected id sent on confirmation.
  await go('')
  await sendFrame({ type: 'final', speech: '确认第一项操作吗？', need_confirm: true, operation_id: 'visual-op-a' })
  await sendFrame({ type: 'final', speech: '确认第二项操作吗？', need_confirm: true, operation_id: 'visual-op-b' })
  await cdp.waitFor("document.querySelector('.au-pending-bar')?.dataset.operationId==='visual-op-a'")
  assert.match(await cdp.eval("document.querySelector('.au-pending-next').textContent"), /另有 1/)
  await cdp.eval("document.querySelector('.au-pending-next').click();true")
  await cdp.waitFor("document.querySelector('.au-pending-bar')?.dataset.operationId==='visual-op-b'")
  await screenshot('two-pending')
  await cdp.eval("document.querySelector('.au-pending-buttons .primary').click();true")
  const confirmed = await cdp.eval('window.__visualSent.at(-1)')
  assert.equal(confirmed.is_confirmation, true)
  assert.equal(confirmed.operation_id, 'visual-op-b')
  await cdp.waitFor("document.querySelector('.au-pending-bar')?.dataset.operationId==='visual-op-a'")
  // Deadline is from the server, corrected for skew, and suppresses the expired action.
  await go('')
  const now = Date.now()
  await sendFrame({ type: 'final', speech: '打开后备箱？', need_confirm: true, operation_id: 'visual-expiring',
    confirm_policy: { operation_id: 'visual-expiring', risk: 'high', allowed_channels: ['touch'],
      action_summary: '打开后备箱', expires_at_ms: now + 1800, server_now_ms: now } })
  await cdp.waitFor("document.querySelector('.au-pending-bar')?.textContent.includes('剩余')")
  await screenshot('server-deadline')
  await cdp.waitFor("!document.querySelector('.au-pending-bar')", 5000)
  // Control status comes from ResultBundle evidence; simulated origin remains visible.
  await go('')
  await sendFrame({ type: 'final', speech: '温度本来就是 24 度。', actions: [{type:'vehicle.control',payload:{command:'hvac.set',temperature:24}}],
    result_bundles:[{version:1,task_id:'control',revision:1,goals:[],results:[{step_id:'s',intent:'hvac.set',status:'ok',answer:'',answer_state:'inline',
      evidence:{ack:'acknowledged',state:'satisfied',observed:'unchanged',verified:true,source_kind:'simulated'}}]}] })
  await cdp.waitFor("document.querySelector('.au-control-status')?.textContent==='本来就是'")
  assert.match(await cdp.eval("document.querySelector('.au-control-result').textContent"), /模拟观测/)
  await screenshot('control-evidence')
  await go('')
  await cdp.typeAndSend('附近有什么餐厅')
  await cdp.waitFor("document.querySelector('.au-pending-bar')?.getAttribute('aria-label')==='待确认 · 当前位置'")
  await sendFrame({type:'final',speech:'一个缺少操作 ID 的服务端确认',need_confirm:true})
  assert.match(await cdp.eval("document.querySelector('.au-pending-title').textContent"), /当前位置/)
  await cdp.eval("document.querySelector('.au-pending-buttons button').click();true")
  await cdp.waitFor("!document.querySelector('.au-pending-bar')")
  assert.equal(await cdp.eval('window.__visualSent.length'), 0, 'denying local consent must not send a service confirmation')
  await cdp.eval("window.__visualSocket.readyState=3;window.__visualSocket.onclose({});true")
  await cdp.waitFor("document.querySelector('.au-conn')?.textContent.includes('云端未连接 · 车控仍可用')", 1000)
  await screenshot('design-offline-copy')
  writeFileSync(join(out,'browser-evidence.json'),JSON.stringify({historyPreserved:true,jumpToLatest:true,confirmationId:confirmed.operation_id,serverDeadline:true,unchangedNotVerified:true,localConsentBound:true,offlineCopyUserApproved:true},null,2))
  console.log('PASS: dialog states, history position, jump to latest, pinned operation identity, deadline, control evidence')
})
