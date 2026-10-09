import test from 'node:test'
import assert from 'node:assert/strict'
import { messageTextForView } from './errorPresentation.mjs'

test('raw gateway errors are opt-in while local recovery guidance and partial answers remain readable', () => {
  assert.equal(messageTextForView({text:'出错了：provider_trace_internal',error:true}), '这次请求没有完成，请稍后重试。')
  assert.equal(messageTextForView({text:'出错了：timeout 8000ms',error:true}), '这次等待时间较长，请稍后重试。')
  assert.equal(messageTextForView({text:'出错了：provider_trace_internal',error:true},true), 'provider_trace_internal')
  const guidance='没有获取到当前位置。请告诉我城市或地点。'
  assert.equal(messageTextForView({text:guidance,error:true}),guidance)
  assert.equal(messageTextForView({text:'已经找到两个地点',error:true}),'已经找到两个地点')
})
