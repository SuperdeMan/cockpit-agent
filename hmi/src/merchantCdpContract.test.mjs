import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const cases = readFileSync(new URL('../../test/hmi_cdp/run_cases.mjs', import.meta.url), 'utf8')
const driver = readFileSync(new URL('../../test/hmi_cdp/driver.mjs', import.meta.url), 'utf8')

test('merchant CDP case locks ordinary card actions and global confirmation frames separately', () => {
  assert.match(cases, /async C7\(cdp\)/)
  assert.match(cases, /is_confirmation === false/)
  assert.match(cases, /is_confirmation === true/)
  assert.match(driver, /async waitReceivedFrame\(/)
  assert.match(cases, /await cdp\.waitReceivedFrame\(/)
  assert.match(cases, /data\.type === 'final'/)
})

test('merchant CDP case checks merchant confirmation and link-only payment wording', () => {
  assert.match(cases, /商户下单/)
  assert.match(cases, /危险操作/)
  assert.match(cases, /安全支付链接/)
  assert.match(cases, /扫码/)
})

test('merchant CDP has an explicit same-session Luckin cancellation cleanup', () => {
  assert.match(cases, /async C8\(cdp\)/)
  assert.match(cases, /CDP_MERCHANT_CANCEL_PROMPT/)
  assert.match(cases, /确认取消/)
  assert.match(cases, /is_confirmation === true/)
  assert.match(cases, /已取消/)
})

test('merchant CDP geolocation is explicitly overrideable for the audited store', () => {
  assert.match(driver, /CDP_LATITUDE/)
  assert.match(driver, /CDP_LONGITUDE/)
})

test('merchant CDP has a read-only query case that cannot create another real order', () => {
  assert.match(cases, /async C9\(cdp\)/)
  assert.match(cases, /CDP_MERCHANT_QUERY_PROMPT/)
  assert.match(cases, /CDP_MERCHANT_EXPECTED_STATUS/)
  assert.match(cases, /waitReceivedFrame/)
  assert.match(cases, /查询结果被通用搜索劫持/)
})
