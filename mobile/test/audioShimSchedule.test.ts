// 排定边界逐帧衔接（语音批 2026-09-06「混杂的嗡嗡声」）。
//
// react-native-audio-api 把 start(when) 换算成帧用的是**截断**：`timeToSampleFrame = static_cast<size_t>(time * sr)`
// （dsp/AudioUtils.hpp）。pcmPlayer.mjs 的起点是浮点累加 `nextStart = when + duration`，落在 n−ε 时就被截成 n−1
// ——这一片早一帧起播、与上一片末样本叠在同一帧。24k→48k 每 42ms 一片，1163 片里数百个边界错帧 ⇒ ~24Hz 的
// 咔哒串混在人声底下，听感就是「嗡嗡」。浏览器按 Web Audio 规范四舍五入，所以 HMI 从来没有这个声音。
// 修在适配层（共享 pcmPlayer.mjs 一行不改）：① 起点吸附到帧格 + 半帧（截断后正好是那一帧）；
// ② `duration` 用**实际提交的目标帧数**/ctx 率而不是 源样本数/源率——重采样器会把尾巴留到下一片，
// 首片就比名义短 2 帧，用名义时长排下一片会空 2 帧。两条合起来：任何源率下相邻片都严丝合缝。
import { PcmPlayer } from '@shared/pcmPlayer.mjs'
import { playerCtxOf } from '@/core/voice/audioCtx'

 

function fakeCtx(sampleRate: number) {
  const starts: { when: number; frames: number }[] = []
  const ctx = {
    sampleRate,
    currentTime: 0,
    destination: {},
    createBuffer(_ch: number, length: number, _sr: number) {
      return { length, copyToChannel() {} }
    },
    createBufferSource() {
      let frames = 0
      const src: any = {
        connect() {},
        start(when: number) {
          starts.push({ when, frames })
        },
        stop() {},
        onEnded: null,
      }
      Object.defineProperty(src, 'buffer', {
        set(b: { length: number }) {
          frames = b.length
        },
      })
      return src
    },
  }
  return { ctx: ctx as any, starts }
}

/** 原生的换算：截断 */
const toFrame = (when: number, sr: number) => Math.trunc(when * sr)

function misalignedBoundaries(starts: { when: number; frames: number }[], sr: number): number {
  let bad = 0
  for (let k = 1; k < starts.length; k += 1) {
    const prev = starts[k - 1]
    const cur = starts[k]
    if (toFrame(cur.when, sr) !== toFrame(prev.when, sr) + prev.frames) bad += 1
  }
  return bad
}

test('24k → 48k（minimax 1024 样本片）：1163 片的排定起点截断成帧后逐片严丝合缝', () => {
  const { ctx, starts } = fakeCtx(48000)
  ctx.currentTime = 12.345678 // 不从 0 起：浮点误差是绝对时刻的函数
  const player = new PcmPlayer({ ctx: playerCtxOf(ctx), sampleRate: 24000, jitterMs: 200 } as never)
  for (let k = 0; k < 1163; k += 1) player.push(new Int16Array(1024).fill(1000))
  expect(starts.length).toBe(1163)
  expect(misalignedBoundaries(starts, 48000)).toBe(0)
})

test('22050 → 48k（cosyvoice 4000 样本片，非整数倍）：提交帧数 8707/8708 交替，起点仍按实际帧数衔接', () => {
  const { ctx, starts } = fakeCtx(48000)
  ctx.currentTime = 3.1
  const player = new PcmPlayer({ ctx: playerCtxOf(ctx), sampleRate: 22050, jitterMs: 200 } as never)
  for (let k = 0; k < 191; k += 1) player.push(new Int16Array(4000).fill(500))
  expect(misalignedBoundaries(starts, 48000)).toBe(0)
})

test('同率（48k 源）不重采样：起点同样吸附到帧格', () => {
  const { ctx, starts } = fakeCtx(48000)
  ctx.currentTime = 7.777
  const player = new PcmPlayer({ ctx: playerCtxOf(ctx), sampleRate: 48000, jitterMs: 200 } as never)
  for (let k = 0; k < 300; k += 1) player.push(new Int16Array(1920).fill(300))
  expect(misalignedBoundaries(starts, 48000)).toBe(0)
})
