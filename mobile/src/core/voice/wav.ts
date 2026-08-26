// WAV 解析（实施计划 M2-3 批处理回退：`POST /api/tts` 返回 base64 WAV）。
//
// 为什么不用 AudioContext.decodeAudioData：那条路在本 App 里是关着的——
// react-native-audio-api 的 FFmpeg 被显式 disable（app.config.ts 里写了理由）。
// 而 WAV 本来就是「44 字节头 + 裸 PCM」，自己解比把 FFmpeg 请回来便宜得多。
// 谨慎处理 chunk 遍历而不是写死 44：有些引擎会插 LIST/fact chunk，
// 写死偏移会把元数据当音频播出刺啦声——**能解析就别按经验值猜**。
export interface WavData {
  pcm: Int16Array
  sampleRate: number
  channels: number
}

/** 解析 s16le PCM 的 WAV；不是这种形态返回 null（调用方静默放弃，不播噪音） */
export function parseWav(bytes: Uint8Array): WavData | null {
  if (bytes.length < 44) return null
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const tag = (off: number): string =>
    String.fromCharCode(bytes[off], bytes[off + 1], bytes[off + 2], bytes[off + 3])
  if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE') return null

  let pos = 12
  let sampleRate = 0
  let channels = 0
  let bits = 0
  let format = 0
  while (pos + 8 <= bytes.length) {
    const id = tag(pos)
    const size = dv.getUint32(pos + 4, true)
    const body = pos + 8
    if (id === 'fmt ') {
      if (size < 16 || body + 16 > bytes.length) return null
      format = dv.getUint16(body, true)
      channels = dv.getUint16(body + 2, true)
      sampleRate = dv.getUint32(body + 4, true)
      bits = dv.getUint16(body + 14, true)
    } else if (id === 'data') {
      if (format !== 1 || bits !== 16 || !sampleRate) return null
      const avail = Math.min(size, bytes.length - body)
      const samples = Math.floor(avail / 2)
      const pcm = new Int16Array(samples)
      for (let i = 0; i < samples; i += 1) pcm[i] = dv.getInt16(body + i * 2, true)
      return { pcm, sampleRate, channels: channels || 1 }
    }
    // chunk 按偶数边界对齐（RIFF 规范），奇数长度后面有一个填充字节
    pos = body + size + (size % 2)
  }
  return null
}

/** 多声道交织 → 单声道（取首声道；TTS 引擎给的都是单声道，这是防御） */
export function toMono(data: WavData): Int16Array {
  if (data.channels <= 1) return data.pcm
  const n = Math.floor(data.pcm.length / data.channels)
  const out = new Int16Array(n)
  for (let i = 0; i < n; i += 1) out[i] = data.pcm[i * data.channels]
  return out
}
