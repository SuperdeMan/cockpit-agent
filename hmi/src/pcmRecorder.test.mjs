// 声纹录音器单测（M4 P4，2026-07-26 真机第三批）。
//
// 这里守的是一条**跨文件的不变量**：注册/自证/识别三条路必须在**同一条音频通路**上。
// 它没法靠类型系统守——两边都是「音频」，一边 webm 一边 PCM，编译期毫无区别，
// 而真机后果是同一个人的余弦掉 0.2、两个人被认成同一个。故用源码级断言钉住。
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { framesToInt16, trimSilence, MIC_CONSTRAINTS } from './pcmRecorder.mjs'

const f = (amp, n = 512) => Float32Array.from({ length: n }, (_, i) => amp * Math.sin(i / 3))

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8')

test('Float32 → Int16 范围与截断', () => {
  const out = framesToInt16([new Float32Array([1.0, -1.0, 0, 2.0, -2.0])])
  assert.equal(out[0], 0x7fff)
  assert.equal(out[1], -0x8000)
  assert.equal(out[2], 0)
  assert.equal(out[3], 0x7fff, '超范围应截断而不是回绕')
  assert.equal(out[4], -0x8000)
})

// ── 静音门控：注册端与识别端必须同样只喂人声 ──────────────────────────────

test('切掉开头和结尾的静音', () => {
  // 注册是「按下按钮 → 4 秒定时」，开口前的犹豫和念完的尾巴都在录音里。
  const out = trimSilence([f(0.002), f(0.001), f(0.3), f(0.25), f(0.0005), f(0.001)])
  assert.equal(out.length, 2)
})

test('**不在语音中间挖洞**——中间的停顿原样保留', () => {
  // 逐帧筛会把辅音/弱元音连同停顿一起丢掉，剩一串爆发音：实测那样的音频喂 ASR
  // 只能转出零星几个字（正常应转出整句），说明它已经不是原来那句话了。
  // 声纹嵌入吃同一份信号，只是它不报错，只会悄悄变得谁都认不准。
  const out = trimSilence([f(0.3), f(0.001), f(0.002), f(0.25)])
  assert.equal(out.length, 4, '中间的低能量帧必须留着，否则把话切碎了')
})

test('全静音返回空——不发一段空气上去', () => {
  assert.deepEqual(trimSilence([f(0.001), f(0.002), f(0.0005)]), [])
})

test('门槛按峰值自适应（麦克风增益低也不至于全丢）', () => {
  // 整体很轻但相对差异明显：轻声说话不该被整段判成静音
  const out = trimSilence([f(0.02), f(0.2), f(0.18)])
  assert.equal(out.length, 2)
})

test('保留原始帧顺序与连续性（顺序错了就是另一段话）', () => {
  const a = f(0.3), mid = f(0.001), b = f(0.25)
  const out = trimSilence([f(0.001), a, mid, b, f(0.0005)])
  assert.deepEqual(out, [a, mid, b])
})

test('多帧按顺序拼接（拼错顺序=另一段话）', () => {
  const out = framesToInt16([new Float32Array([1.0]), new Float32Array([-1.0, 0])])
  assert.deepEqual([...out], [0x7fff, -0x8000, 0])
})

test('与识别器的转换口径逐字一致', () => {
  // voiceprintIdentifier._flatten 是主链路探针的转换；两处若有一处写成 /0x8000
  // 或漏了截断，模板与探针就差一个系统性缩放——余弦不会报错，只会悄悄变低。
  const src = read('./voiceprintIdentifier.mjs')
  assert.ok(src.includes('s < 0 ? s * 0x8000 : s * 0x7fff'),
            '识别器的 Float32→Int16 口径变了，pcmRecorder 要同步')
})

test('采集约束与 VAD 主链路逐字一致（同一条信道）', () => {
  const want = MIC_CONSTRAINTS.audio
  assert.deepEqual(want, { echoCancellation: true, noiseSuppression: true, autoGainControl: true })
  for (const f of ['./vadEngine.ts', './handsFreeController.ts']) {
    const src = read(f)
    const hit = src.includes(
      'echoCancellation: true, noiseSuppression: true, autoGainControl: true')
    assert.ok(hit, `${f} 的 getUserMedia 约束与注册端不一致 = 模板与探针不在一条信道上`)
  }
})

test('注册与自证都不得走 MediaRecorder/webm', () => {
  // 真机第三批的根因：注册走 webm(opus 有损)、识别走原始 PCM，同人余弦 0.73→0.48。
  // 「试一试」尤其不能走另一条路——它是唯一的自证手段，走岔了就成了假证人。
  const api = read('./audio.ts')
  const enroll = api.slice(api.indexOf('export async function enrollVoiceprint'))
    .slice(0, 900)
  assert.ok(enroll.includes("mime = 'pcm16le'"), '注册默认格式必须是 pcm16le')
  const ident = api.slice(api.indexOf('export async function identifySpeaker')).slice(0, 900)
  assert.ok(ident.includes("mime = 'pcm16le'"), '「试一试」必须与主链路同通路')

  const panel = read('./components/SettingsPanel.tsx')
  assert.ok(panel.includes('new PcmRecorder()'), '设置页录音器必须是 PcmRecorder')
  assert.ok(!/new MicController\(\)/.test(panel), '设置页不得再用 MediaRecorder 录声纹')
})

test('重录必须能带原 occupant_id（否则记忆分家）', () => {
  const api = read('./audio.ts')
  assert.ok(api.includes('occupant_id=${encodeURIComponent(occupantId)}'),
            '没有 occupant_id 通道，已录的人重录只能新建一个身份')
  const panel = read('./components/SettingsPanel.tsx')
  assert.ok(panel.includes('targetOcc'), '设置页要能把重录绑定到已有乘员')
})
