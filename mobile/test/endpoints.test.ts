// 云端点校验与派生（实施计划 M0-5）：判据与 scripts/dev_stack_lib.py:346-365 同构，
// 测例合法/非法各 ≥3。
import { cloudEndpoints, edgeWsUrl, isValidTailnetFqdn } from '@/core/config/endpoints'

describe('isValidTailnetFqdn', () => {
  test.each([
    'car.tail1234.ts.net',
    'dev-stack.tail0abc.ts.net',
    'x.ts.net',
  ])('合法：%s', (fqdn) => {
    expect(isValidTailnetFqdn(fqdn)).toBe(true)
  })

  test.each([
    ['Car.tail1234.ts.net', '大写'],
    ['foo.example.com', '非 ts.net 后缀'],
    ['-a.tail.ts.net', '首字符连字符'],
    ['a..ts.net', '空 label（整串正则过、label 校验拦）'],
    ['a-.tail.ts.net', 'label 以连字符结尾'],
    [`${'a'.repeat(64)}.ts.net`, 'label 超 63'],
  ])('非法：%s（%s）', (fqdn) => {
    expect(isValidTailnetFqdn(fqdn)).toBe(false)
  })

  test('总长 >253 非法（每个 label 都合法，只有总长超）', () => {
    const label = 'a'.repeat(62) // 单 label ≤63 合法
    const fqdn = `${label}.${label}.${label}.${label}.ts.net` // 62*4+3+7=258
    expect(fqdn.length).toBeGreaterThan(253)
    expect(isValidTailnetFqdn(fqdn)).toBe(false)
  })
})

describe('cloudEndpoints', () => {
  test('派生 8443/8444 两条 https URL', () => {
    expect(cloudEndpoints('car.tail1234.ts.net')).toEqual({
      edgeUrl: 'https://car.tail1234.ts.net:8443',
      audioUrl: 'https://car.tail1234.ts.net:8444',
    })
  })

  test('非法 FQDN 抛错（与 dev_stack_lib 同款 fail-closed）', () => {
    expect(() => cloudEndpoints('foo.example.com')).toThrow()
  })
})

describe('edgeWsUrl', () => {
  test('https→wss + /ws + token urlencode', () => {
    expect(edgeWsUrl('https://car.tail1234.ts.net:8443', 'tok en')).toBe(
      'wss://car.tail1234.ts.net:8443/ws?token=tok%20en',
    )
  })
  test('http→ws（dev 档 LAN 明文）', () => {
    expect(edgeWsUrl('http://192.168.1.10:18000', 't')).toBe(
      'ws://192.168.1.10:18000/ws?token=t',
    )
  })
})
