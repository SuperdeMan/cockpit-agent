// 语音面纯编解码单测（实施计划 M2-2/M2-3）：重采样 / base64 / WAV 解析。
// 这三个都是「错了不报错、只出坏数据」的那类：采样率不对听成变速、base64 错一位
// 服务端只回 error、WAV 头猜偏就播出刺啦声——所以它们必须有机器判据。
import { bytesToBase64, base64ToBytes } from '@/core/voice/base64'
import { Resampler } from '@/core/voice/resample'
import { parseWav, toMono } from '@/core/voice/wav'
import { int16ToWav } from '@shared/pcmRing.mjs'

describe('Resampler', () => {
  test('同采样率直通（同一个对象原样返回，不做无谓拷贝）', () => {
    const r = new Resampler(16000, 16000)
    expect(r.passthrough).toBe(true)
    const f = new Float32Array([0.1, 0.2, 0.3])
    expect(r.process(f)).toBe(f)
  })

  test('48k→16k：长度约 1/3', () => {
    const r = new Resampler(48000)
    const out = r.process(new Float32Array(4800))
    expect(out.length).toBeGreaterThanOrEqual(1598)
    expect(out.length).toBeLessThanOrEqual(1601)
  })

  test('22050→48000 上采样：长度约 2.18 倍（TTS 下行那条）', () => {
    const r = new Resampler(22050, 48000)
    const out = r.process(new Float32Array(2205))
    expect(out.length).toBeGreaterThanOrEqual(4798)
    expect(out.length).toBeLessThanOrEqual(4801)
  })

  test('跨帧连续：分两帧送 ≈ 一次性送（相位与末样本留存的实证）', () => {
    const n = 960
    const src = new Float32Array(n)
    for (let i = 0; i < n; i += 1) src[i] = Math.sin((2 * Math.PI * 100 * i) / 48000)
    const whole = new Resampler(48000).process(src)
    const split = new Resampler(48000)
    const a = split.process(src.subarray(0, n / 2))
    const b = split.process(src.subarray(n / 2))
    const joined = new Float32Array(a.length + b.length)
    joined.set(a, 0)
    joined.set(b, a.length)
    expect(Math.abs(joined.length - whole.length)).toBeLessThanOrEqual(1)
    const m = Math.min(joined.length, whole.length)
    for (let i = 0; i < m; i += 1) expect(Math.abs(joined[i] - whole[i])).toBeLessThan(0.02)
  })

  test('返回的是独立数组不是视图（copyToChannel 会按底层 buffer 大小判空间）', () => {
    const out = new Resampler(48000).process(new Float32Array(4800))
    expect(out.buffer.byteLength).toBe(out.length * 4)
  })

  test('reset 清相位：换一段录音不带上一段的尾巴', () => {
    const r = new Resampler(44100)
    r.process(new Float32Array(441).fill(0.9))
    r.reset()
    const out = r.process(new Float32Array(441))
    for (const v of out) expect(Math.abs(v)).toBeLessThan(1e-6)
  })
})

describe('base64', () => {
  test('往返：三种余数长度都对（0/1/2）', () => {
    for (const n of [0, 1, 2, 3, 4, 5, 255, 1000]) {
      const bytes = new Uint8Array(n)
      for (let i = 0; i < n; i += 1) bytes[i] = (i * 37) % 256
      const round = base64ToBytes(bytesToBase64(bytes))
      expect(Array.from(round)).toEqual(Array.from(bytes))
    }
  })

  test('编码结果与已知向量一致', () => {
    expect(bytesToBase64(new Uint8Array([104, 105]))).toBe('aGk=')
    expect(bytesToBase64(new Uint8Array([102, 111, 111]))).toBe('Zm9v')
  })

  test('解码忽略换行与空白（服务端换个 JSON 编码器就会多出来）', () => {
    expect(Array.from(base64ToBytes('Zm9v\n Zm9v'))).toEqual([102, 111, 111, 102, 111, 111])
  })
})

describe('parseWav', () => {
  test('解出 int16ToWav 造的 WAV（与共享模块互为逆运算）', () => {
    const pcm = new Int16Array([0, 1000, -1000, 32767, -32768])
    const parsed = parseWav(int16ToWav(pcm, 16000))
    expect(parsed).not.toBeNull()
    expect(parsed!.sampleRate).toBe(16000)
    expect(parsed!.channels).toBe(1)
    expect(Array.from(parsed!.pcm)).toEqual(Array.from(pcm))
  })

  test('data 之前插一个 LIST chunk 也能解（写死 44 偏移就会把元数据当音频播）', () => {
    const pcm = new Int16Array([1, 2, 3, 4])
    const base = int16ToWav(pcm, 22050)
    const extra = 12 // 'LIST' + size + 4 字节内容
    const out = new Uint8Array(base.length + extra)
    out.set(base.subarray(0, 36), 0) // RIFF 头 + fmt chunk
    const dv = new DataView(out.buffer)
    out[36] = 76; out[37] = 73; out[38] = 83; out[39] = 84 // 'LIST'
    dv.setUint32(40, 4, true)
    out.set(base.subarray(36), 36 + extra) // data chunk 原样接在后面
    dv.setUint32(4, out.length - 8, true)
    const parsed = parseWav(out)
    expect(parsed).not.toBeNull()
    expect(parsed!.sampleRate).toBe(22050)
    expect(Array.from(parsed!.pcm)).toEqual([1, 2, 3, 4])
  })

  test('非 WAV / 截断 / 非 PCM16 一律 null（宁可不播也不播噪音）', () => {
    expect(parseWav(new Uint8Array(10))).toBeNull()
    expect(parseWav(new Uint8Array(64))).toBeNull()
    const wav = int16ToWav(new Int16Array([1, 2]), 16000)
    const float = new Uint8Array(wav)
    new DataView(float.buffer).setUint16(20, 3, true) // audioFormat=3（IEEE float）
    expect(parseWav(float)).toBeNull()
  })

  test('toMono 取首声道', () => {
    expect(Array.from(toMono({ pcm: new Int16Array([1, 9, 2, 9, 3, 9]), sampleRate: 16000, channels: 2 }))).toEqual([1, 2, 3])
  })
})
