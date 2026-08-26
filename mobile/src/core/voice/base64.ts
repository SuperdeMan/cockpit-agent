// 字节 → base64（实施计划 M2-2 批处理兜底：`POST /api/asr` 的 body 是 base64 音频）。
//
// 为什么自己写而不是 btoa：Hermes 的 btoa 要先把字节拼成 binary string，5 秒 16k
// s16le ≈ 160KB ⇒ 16 万次 fromCharCode + 一个 160KB 字符串，纯为了绕一圈。
// 纯函数、零依赖、node 可单测（test/voice.test.ts）。
const CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

export function bytesToBase64(bytes: Uint8Array): string {
  let out = ''
  const n = bytes.length
  const tail = n % 3
  const main = n - tail
  for (let i = 0; i < main; i += 3) {
    const v = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2]
    out += CHARS[(v >> 18) & 63] + CHARS[(v >> 12) & 63] + CHARS[(v >> 6) & 63] + CHARS[v & 63]
  }
  if (tail === 1) {
    const v = bytes[main] << 16
    out += CHARS[(v >> 18) & 63] + CHARS[(v >> 12) & 63] + '=='
  } else if (tail === 2) {
    const v = (bytes[main] << 16) | (bytes[main + 1] << 8)
    out += CHARS[(v >> 18) & 63] + CHARS[(v >> 12) & 63] + CHARS[(v >> 6) & 63] + '='
  }
  return out
}

const LOOKUP = (() => {
  const t = new Int16Array(128).fill(-1)
  for (let i = 0; i < CHARS.length; i += 1) t[CHARS.charCodeAt(i)] = i
  return t
})()

/** base64 → 字节（批处理 TTS 的 `{audio: base64 wav}` 回来这条路）。
 *  非法字符一律跳过（含换行/空格）——服务端换个 JSON 编码器就多出换行的事发生过。 */
export function base64ToBytes(s: string): Uint8Array {
  const out = new Uint8Array(Math.floor((s.length * 3) / 4) + 3)
  let n = 0
  let acc = 0
  let bits = 0
  for (let i = 0; i < s.length; i += 1) {
    const c = s.charCodeAt(i)
    const v = c < 128 ? LOOKUP[c] : -1
    if (v < 0) continue // '=' 与任何噪声都在这被丢掉
    acc = (acc << 6) | v
    bits += 6
    if (bits >= 8) {
      bits -= 8
      out[n] = (acc >> bits) & 0xff
      n += 1
    }
  }
  return out.subarray(0, n)
}
