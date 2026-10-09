import test from 'node:test'
import assert from 'node:assert/strict'
import { selectStage } from './stagePresentation.mjs'
import { drivingCardSummary } from './cardPresentation.mjs'
test('driving blocks reading/payment and a cancelled latest route never restores an older route', () => {
  const route={type:'route_plan',destination:'A'}
  const pay={type:'payment_qr',amount:'1元'}
  const history=[{uiCard:route},{uiCard:pay}]
  assert.equal(selectStage(history,false,pay).kind,'payment')
  assert.equal(selectStage(history,true,pay).kind,'map')
  assert.equal(selectStage([...history,{uiCard:{...route,cancelled:true}}],true,pay).kind,'idle')
  assert.equal(selectStage([{uiCard:pay}],true,pay).kind,'idle')
})
test('reminder summary reads the existing item/actions contract', () => {
  const card={type:'reminder_card',item:{title:'评审会',time_display:'今天 14:50'},actions:[{label:'知道了',send_text:'完成提醒：评审会'}]}
  const result=drivingCardSummary(card)
  assert.equal(result.title,'评审会')
  assert.equal(result.main,'今天 14:50')
  assert.equal(result.button.text,'完成提醒：评审会')
})
