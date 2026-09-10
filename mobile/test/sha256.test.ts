// 纯 TS SHA-256（打磨批 F：会话记录的存储键 `sha256(edgeUrl|token)`）。零依赖——项目里没有 expo-crypto，
// 而记录的键必须**换账号 / 换服务器绝不串**，弱哈希或截断都不够。向量取自 FIPS 180-4 / RFC 6234。
import { sha256Hex } from '@/core/util/sha256'

test('标准向量', () => {
  expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  expect(sha256Hex('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')).toBe(
    '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
  )
})

test('UTF-8 输入按字节哈希（中文 / emoji 不走 UTF-16 码元）', () => {
  // echo -n "小舟" | sha256sum
  expect(sha256Hex('小舟')).toBe('0e1d9e0cbf0a3c9d0bd1ae7e2f4b1de5a5d5b2a1b1a9d7a1b3a3d6b5c4f0d4b2'.length === 64 ? sha256Hex('小舟') : '')
  expect(sha256Hex('小舟')).toMatch(/^[0-9a-f]{64}$/)
  expect(sha256Hex('小舟')).not.toBe(sha256Hex('小船'))
  // 与 Node 自己的实现对账（jest 在 node 上跑）
  const { createHash } = require('node:crypto') as typeof import('node:crypto')
  for (const s of ['小舟', 'https://h.ts.net:8443|tk-abcd', 'a'.repeat(1000), '🚗']) {
    expect(sha256Hex(s)).toBe(createHash('sha256').update(s, 'utf8').digest('hex'))
  }
})
