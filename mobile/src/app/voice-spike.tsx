// 语音采集/播放 spike 屏（实施计划 M2-1 采集定案 + M2-3 注入契约取证）。
//
// 为什么是一屏而不是一个 node 脚本：判据全部只在真机上成立——帧间隔分布、实际采样率、
// getChannelData 是不是可写视图、22050 的 buffer 在 48k 上下文里会不会被重采样。
// 模拟器与 node 都测不出来，README 星数更测不出来（计划 M2-1：A/B 都跑过才许选）。
//
// 入口（不进主导航，避免给正式 UI 加一个只有开发者用的按钮）：
//   adb shell am start -a android.intent.action.VIEW -d "xiaozhou://voice-spike"
//
// 读数出口刻意留两条：屏上一行一条摘要（uiautomator dump 拿得到**文本**，不用从截图
// 里认数字），console 打同一条（Metro 终端）。
import { useCallback, useRef, useState } from 'react'
import { Pressable, ScrollView, Text, View } from 'react-native'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { loadServerConfig } from '@/core/config/storage'
import { settingsStore } from '@/core/settings/store'
import { AsrSession, recognizeBatch } from '@/core/voice/asr'
import { TtsSession, synthesizeBatch } from '@/core/voice/tts'
import { newPcmPlayer, playerCtxOf, sharedAudioContext } from '@/core/voice/audioCtx'
import { audioFocusInstalled, audioFocusLog } from '@/core/voice/audioFocus'
import { handsFreeAvailability } from '@/core/voice/handsFree'
import { DEFAULT_KEYWORDS, KwsEngine, kwsBusy, kwsNativeAvailable } from '@/core/voice/kws'
import { micBusStats, micLease } from '@/core/voice/micBus'
import { recorder } from '@/core/voice/recorder'
import { VadEngine, vadNativeAvailable } from '@/core/voice/vad'
import { speechController } from '@/core/voice/speech'
import { Resampler } from '@/core/voice/resample'
import { parseWav, toMono } from '@/core/voice/wav'
import { usePalette } from '@/ui/theme'

/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-require-imports */

const RECORD_MS = 5000

// ── 统计小工具（帧间隔分布是采集定案的主判据：计划要求帧间隔 ≤128ms 稳定）──
function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * q)))
  return sorted[i]
}

function gapStats(times: number[]): string {
  if (times.length < 2) return 'frames=' + times.length + ' (不足两帧，无间隔)'
  const gaps: number[] = []
  for (let i = 1; i < times.length; i += 1) gaps.push(times[i] - times[i - 1])
  const sorted = [...gaps].sort((a, b) => a - b)
  const mean = gaps.reduce((s, g) => s + g, 0) / gaps.length
  return (
    'frames=' + times.length +
    ' gap min=' + sorted[0] +
    ' p50=' + quantile(sorted, 0.5) +
    ' p95=' + quantile(sorted, 0.95) +
    ' max=' + sorted[sorted.length - 1] +
    ' mean=' + mean.toFixed(1) + 'ms'
  )
}

/** base64 串对应的字节数（候选 A 的 data 事件给的是 base64 PCM） */
function b64Bytes(s: string): number {
  const pad = s.endsWith('==') ? 2 : s.endsWith('=') ? 1 : 0
  return Math.floor((s.length * 3) / 4) - pad
}

export default function VoiceSpikeScreen() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const [lines, setLines] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const ctxRef = useRef<any>(null)

  const log = useCallback((s: string) => {
    // eslint-disable-next-line no-console
    console.log('[voice-spike]', s)
    setLines((prev) => [...prev, s])
  }, [])

  /** AudioContext 单例（探测之间复用）。走 sharedAudioContext 而不是自己 new——
   *  正式播报用的就是它，spike 要测的是**同一个对象**（自己 new 一个就测不到
   *  它建完是 suspended 这类事，首轮 M2-1 正是这么差点读错重采样那条） */
  const audioCtx = useCallback((): any => {
    if (!ctxRef.current) ctxRef.current = sharedAudioContext()
    return ctxRef.current
  }, [])

  const ensurePermission = useCallback(async (): Promise<boolean> => {
    try {
      const { AudioManager } = require('react-native-audio-api')
      const status = await AudioManager.requestRecordingPermissions()
      log('perm: ' + status)
      return status === 'Granted'
    } catch (e: any) {
      log('perm FAIL: ' + (e?.message ?? e))
      return false
    }
  }, [log])

  // ── 候选 A：react-native-audio-record（legacy bridge 模块，2022-05 停更）─────
  const probeA = useCallback(async () => {
    setBusy('A')
    try {
      if (!(await ensurePermission())) return
      // 懒加载：它在模块顶层就 new NativeEventEmitter(NativeModules.RNAudioRecord)，
      // 模块没注册时那句会抛——顶层 import 会把整屏带崩，所以 require 进 try
      let AudioRecord: any
      try {
        AudioRecord = require('react-native-audio-record').default
      } catch (e: any) {
        log('A: require 抛错 = ' + (e?.message ?? e))
        return
      }
      const { NativeModules } = require('react-native')
      log('A: NativeModules.RNAudioRecord = ' + (NativeModules.RNAudioRecord ? 'present' : 'MISSING'))
      const times: number[] = []
      let bytes = 0
      try {
        AudioRecord.init({
          sampleRate: 16000,
          channels: 1,
          bitsPerSample: 16,
          audioSource: 6, // VOICE_RECOGNITION
          wavFile: 'spike-a.wav',
        })
        AudioRecord.on('data', (b64: string) => {
          times.push(Date.now())
          bytes += b64Bytes(b64)
        })
        AudioRecord.start()
      } catch (e: any) {
        log('A: init/start 抛错 = ' + (e?.message ?? e))
        return
      }
      await new Promise((r) => setTimeout(r, RECORD_MS))
      try {
        await AudioRecord.stop()
      } catch (e: any) {
        log('A: stop 抛错 = ' + (e?.message ?? e))
      }
      const secs = bytes / 2 / 16000
      log('A: ' + gapStats(times) + ' bytes=' + bytes + ' ~' + secs.toFixed(2) + 's@16k/s16le')
    } finally {
      setBusy('')
    }
  }, [ensurePermission, log])

  // ── 候选 B：react-native-audio-api 的 AudioRecorder ────────────────────────
  //  ms 参数给长测用：计划判据里的「30 分钟无爆音/漂移」，爆音只有人耳能判，
  //  **漂移可以机器判**——采集到的样本数换算成秒 vs 墙钟经过的秒，偏差就是漂移。
  const probeB = useCallback(async (ms: number = RECORD_MS) => {
    setBusy('B')
    try {
      if (!(await ensurePermission())) return
      const { AudioRecorder } = require('react-native-audio-api')
      const rec = new AudioRecorder()
      const times: number[] = []
      let frames = 0
      const rates = new Set<number>()
      const lens = new Set<number>()
      let peak = 0
      rec.onAudioReady({ sampleRate: 16000, bufferLength: 1600, channelCount: 1 }, (ev: any) => {
        times.push(Date.now())
        frames += ev.numFrames ?? 0
        rates.add(ev.buffer?.sampleRate ?? -1)
        lens.add(ev.numFrames ?? -1)
        const d = ev.buffer?.getChannelData?.(0)
        if (d) for (let i = 0; i < d.length; i += 37) peak = Math.max(peak, Math.abs(d[i]))
      })
      rec.onError?.((e: any) => log('B: recorderError = ' + (e?.message ?? JSON.stringify(e))))
      const wallStart = Date.now()
      await rec.start()
      await new Promise((r) => setTimeout(r, ms))
      await rec.stop()
      const wallMs = Date.now() - wallStart
      rec.clearOnAudioReady?.()
      const audioMs = (frames / 16000) * 1000
      const driftPct = wallMs ? ((audioMs - wallMs) / wallMs) * 100 : 0
      log(
        'B: ' + gapStats(times) +
        ' sr=' + [...rates].join('/') +
        ' bufLen=' + [...lens].join('/') +
        ' totalFrames=' + frames +
        ' ~' + (frames / 16000).toFixed(2) + 's peak=' + peak.toFixed(3) +
        ' 漂移=' + driftPct.toFixed(2) + '%（音频秒 vs 墙钟秒）',
      )
    } catch (e: any) {
      log('B: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [ensurePermission, log])

  // ── 注入契约（M2-3 前置）：pcmPlayer.mjs 对 ctx 的全部要求逐条验 ────────────
  const probeInject = useCallback(async () => {
    setBusy('inject')
    try {
      const ctx = audioCtx()
      // resume 是异步的：不 await 就读 state，读到的永远是建出来那一刻的 suspended
      // （首轮 M2-1 就这么读的，量出来的 onEnded 时刻在两个预期值之间，谁也不是）
      await ctx.resume()
      log('ctx: sampleRate=' + ctx.sampleRate + ' state=' + ctx.state + ' currentTime=' + ctx.currentTime.toFixed(3))

      // ① createBuffer + getChannelData 可写性（pcmPlayer 走 getChannelData(0).set(f32)）
      const buf = ctx.createBuffer(1, 8, 22050)
      const view = buf.getChannelData(0)
      view.set([0.11, 0.22, 0.33, 0.44, 0, 0, 0, 0])
      const back = buf.getChannelData(0)
      const writable = Math.abs(back[1] - 0.22) < 1e-3
      log('(1) getChannelData 可写视图 = ' + writable + '（false ⇒ 必须 copyToChannel，适配层已按 false 写）')
      log('    buf.duration=' + buf.duration + ' 期望=' + 8 / 22050 + ' copyToChannel=' + typeof buf.copyToChannel)

      // ② source 的结束回调属性名（浏览器 onended / audio-api onEnded）
      const src = ctx.createBufferSource()
      const props: string[] = []
      let o = Object.getPrototypeOf(src)
      while (o && o !== Object.prototype) {
        props.push(...Object.getOwnPropertyNames(o))
        o = Object.getPrototypeOf(o)
      }
      log('(2) onended=' + props.includes('onended') + ' onEnded=' + props.includes('onEnded') +
        ' start=' + props.includes('start') + ' stop=' + props.includes('stop'))

      // ③ 重采样：播 1 秒的 22050 buffer，量 onEnded 时刻
      const sr = 22050
      const one = ctx.createBuffer(1, sr, sr)
      const ch = one.getChannelData(0)
      for (let i = 0; i < sr; i += 1) ch[i] = 0.2 * Math.sin((2 * Math.PI * 440 * i) / sr)
      if (typeof one.copyToChannel === 'function') one.copyToChannel(ch, 0, 0)
      const s2 = ctx.createBufferSource()
      s2.buffer = one
      s2.connect(ctx.destination)
      const t0 = Date.now()
      const noResampleMs = Math.round((sr / ctx.sampleRate) * 1000)
      s2.onEnded = () => log('(3) onEnded @' + (Date.now() - t0) + 'ms（重采样=~1000 / 不重采样=~' + noResampleMs + '）')
      s2.onended = () => log('(3) 浏览器名 onended 也触发了')
      s2.start(ctx.currentTime)
      await new Promise((r) => setTimeout(r, 1600))
      // 复测一次：单次读数落在两个预期值中间时，先问是不是测量本身不稳
      const s3 = ctx.createBufferSource()
      s3.buffer = one
      s3.connect(ctx.destination)
      const t1 = Date.now()
      s3.onEnded = () => log('(3b) 复测 onEnded @' + (Date.now() - t1) + 'ms')
      s3.start(ctx.currentTime)
      await new Promise((r) => setTimeout(r, 1600))
      log('(3) 若上面没有 onEnded 行 ⇒ 结束回调根本没触发（pcmPlayer 的 sources 不回收）')

      // ④ 同一段音频**经适配层**再播一次：适配层负责重采样到 ctx 率，
      //    这次 onEnded 应该回到 ~1000ms。③ 与 ④ 的差就是适配层干的活
      const pc = playerCtxOf(ctx)
      const b4 = pc.createBuffer(1, sr, sr)
      b4.getChannelData(0).set(ch)
      const s4 = pc.createBufferSource()
      s4.buffer = b4
      s4.connect(pc.destination)
      const t2 = Date.now()
      s4.onended = () => log('(4) 经适配层 onEnded @' + (Date.now() - t2) + 'ms（期望 ~1000）')
      s4.start(pc.currentTime)
      await new Promise((r) => setTimeout(r, 1800))
      log('(4) buf.duration=' + b4.duration.toFixed(3) + '（期望 1.000）')
    } catch (e: any) {
      log('inject: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [audioCtx, log])

  // ── 真实 pcmPlayer + 适配层：合成分片喂进去，验「共享模块零改动可用」────────
  const probePlayer = useCallback(async () => {
    setBusy('player')
    try {
      const ctx = audioCtx()
      const sr = 22050
      const player = newPcmPlayer({
        sampleRate: sr,
        onFirstAudio: () => log('player: onFirstAudio'),
        onUnderrun: () => log('player: underrun'),
      })
      // 15 片 × 100ms：模拟流式 TTS 的到达节奏（每片 2205 samples）
      const chunk = 2205
      for (let k = 0; k < 15; k += 1) {
        const i16 = new Int16Array(chunk)
        const freq = 330 + k * 20
        for (let i = 0; i < chunk; i += 1) {
          i16[i] = Math.round(8000 * Math.sin((2 * Math.PI * freq * (k * chunk + i)) / sr))
        }
        const when = player.push(i16)
        if (k === 0) log('player: 首片 when=' + (when === null ? 'null' : when.toFixed(3)) + ' now=' + ctx.currentTime.toFixed(3))
        await new Promise((r) => setTimeout(r, 100))
      }
      log('player: drainedAt=' + player.drainedAt().toFixed(3) +
        ' remain=' + player.remainingSec().toFixed(3) + 's underruns=' + player.underruns)
      await new Promise((r) => setTimeout(r, 1200))
      player.stop()
      log('player: stop() 完成（听到 15 段上行音阶 = 共享 pcmPlayer 零改动可用）')
    } catch (e: any) {
      log('player: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [audioCtx, log])

  // ── 并存：录音与播放同时开（计划判据「与播放并存不互踢」）────────────────
  const probeCoexist = useCallback(async () => {
    setBusy('coexist')
    try {
      if (!(await ensurePermission())) return
      const { AudioRecorder } = require('react-native-audio-api')
      const ctx = audioCtx()
      const sr = 22050
      const player = newPcmPlayer({ sampleRate: sr })
      const rec = new AudioRecorder()
      const times: number[] = []
      let peak = 0
      rec.onAudioReady({ sampleRate: 16000, bufferLength: 1600, channelCount: 1 }, (ev: any) => {
        times.push(Date.now())
        const d = ev.buffer?.getChannelData?.(0)
        if (d) for (let i = 0; i < d.length; i += 37) peak = Math.max(peak, Math.abs(d[i]))
      })
      await rec.start()
      for (let k = 0; k < 30; k += 1) {
        const i16 = new Int16Array(2205)
        for (let i = 0; i < i16.length; i += 1) {
          i16[i] = Math.round(6000 * Math.sin((2 * Math.PI * 440 * (k * 2205 + i)) / sr))
        }
        player.push(i16)
        await new Promise((r) => setTimeout(r, 100))
      }
      await rec.stop()
      rec.clearOnAudioReady?.()
      player.stop()
      log('coexist: 录音 ' + gapStats(times) + ' peak=' + peak.toFixed(3) + '（播放同时进行，两者都没停=不互踢）')
    } catch (e: any) {
      log('coexist: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [audioCtx, ensurePermission, log])

  // ── e2e 声学回环：**系统说给自己听**（M2-2/M2-3 联合验收的自动化版）──
  //  合成一句话 → 扬声器放 → 麦克风收 → 流式 ASR → 比对定稿文本。
  //  一个按钮同时覆盖：TTS 合成链路 / 播放链路 / 采集 / ASR 流式 / 定稿。
  //  ⚠ 若识别不到而播放正常，那**不是失败读数**——多半是设备 AEC 把扬声器的声音消掉了，
  //  而那正是「播报中按 PTT 不会自听」想要的物理隔离。两种结果都要记进实施记录。
  const probeLoopback = useCallback(async () => {
    setBusy('loopback')
    try {
      const cfg = await loadServerConfig()
      if (!cfg?.audioUrl) {
        log('loopback: 未配置服务器（先在 onboarding 里配）')
        return
      }
      if (!(await ensurePermission())) return
      const s = settingsStore.getState().settings
      const SAY = '今天深圳天气怎么样'
      const t0 = Date.now()
      const out = await synthesizeBatch(
        { audioUrl: cfg.audioUrl, provider: s.ttsProvider, voice: s.voiceId },
        SAY,
      )
      if (!out) {
        log('loopback: TTS 合成失败（网络/引擎 key）')
        return
      }
      log('loopback: 合成 ' + (Date.now() - t0) + 'ms, ' +
        (out.pcm.length / out.sampleRate).toFixed(2) + 's @' + out.sampleRate)

      // 直接用 recorder 收（不经 AsrSession）：**先回答「麦克风到底收没收到扬声器的声音」**，
      // peak 贴近环境底噪(~0.06)=没收到（AEC/静音/音量），peak 高=收到了但识别不出，
      // 两种失败的修法完全不同。识别随后走批处理，同一段音频只走一条路，读数不打架。
      const chunks: Int16Array[] = []
      let peak = 0
      let energy = 0
      let n = 0
      const rec = recorder()
      await rec.start((frame) => {
        chunks.push(frame)
        for (let i = 0; i < frame.length; i += 7) {
          const v = Math.abs(frame[i]) / 32768
          if (v > peak) peak = v
          energy += v * v
          n += 1
        }
      })
      const player = newPcmPlayer({ sampleRate: out.sampleRate })
      player.push(out.pcm)
      const playMs = player.remainingSec() * 1000
      await new Promise((r) => setTimeout(r, playMs + 700))
      player.stop()
      await rec.stop()

      let total = 0
      for (const c of chunks) total += c.length
      const merged = new Int16Array(total)
      let o = 0
      for (const c of chunks) {
        merged.set(c, o)
        o += c.length
      }
      const rms = n ? Math.sqrt(energy / n) : 0
      log('loopback: 录到 ' + (total / 16000).toFixed(2) + 's peak=' + peak.toFixed(3) +
        ' rms=' + rms.toFixed(4) + '（环境底噪 peak~0.06 / rms~0.005）')

      const t1 = Date.now()
      let text = ''
      try {
        text = await recognizeBatch(cfg.audioUrl, merged, s.asrLanguage)
      } catch (e: any) {
        log('loopback: 识别抛错 = ' + (e?.message ?? e))
      }
      log('loopback: 批处理识别 ' + (Date.now() - t1) + 'ms → 「' + text + '」')
      log('loopback: 原句「' + SAY + '」' +
        (text === SAY ? ' ✓ 逐字一致' : text ? ' ≠（看是否只是标点/同音差异）' : ' ✗ 没识别到'))
    } catch (e: any) {
      log('loopback: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [ensurePermission, log])

  // ── ASR 直灌：把合成好的干净音频**当作麦克风的输出**喂给 AsrSession ──
  //  它把两件事分开：① App 的 ASR 协议实现（start 帧/聚包/二进制上行/partial/final/
  //  单 final 守卫）在真栈上对不对；② 麦克风收到的音质够不够识别。
  //  声学回环一次失败会同时怀疑这两件事，而它们的修法毫不相干——所以要有一条
  //  「不经过麦克风」的路。这条通过 = M2-2 的上行链路在真栈上成立。
  const probeAsrInject = useCallback(async () => {
    setBusy('asr-inject')
    try {
      const cfg = await loadServerConfig()
      if (!cfg?.audioUrl) {
        log('asr-inject: 未配置服务器')
        return
      }
      const s = settingsStore.getState().settings
      const SAY = '今天深圳天气怎么样'
      const out = await synthesizeBatch(
        { audioUrl: cfg.audioUrl, provider: s.ttsProvider, voice: s.voiceId },
        SAY,
      )
      if (!out) {
        log('asr-inject: TTS 合成失败')
        return
      }
      // 22050 → 16000（ASR 直传路径不看 sample_rate，必须自己归一）
      const f32 = new Float32Array(out.pcm.length)
      for (let i = 0; i < out.pcm.length; i += 1) f32[i] = out.pcm[i] / 32768
      const at16k = new Resampler(out.sampleRate, 16000).process(f32)
      const i16 = new Int16Array(at16k.length)
      for (let i = 0; i < at16k.length; i += 1) i16[i] = Math.round(at16k[i] * 32767)
      log('asr-inject: 喂 ' + (i16.length / 16000).toFixed(2) + 's @16k')

      let sink: ((f: Int16Array) => void) | null = null
      const fakeRec = {
        recording: false,
        deviceRate: 16000,
        async start(onFrame: (f: Int16Array) => void) {
          sink = onFrame
        },
        async stop() {
          sink = null
        },
      }
      const partials: string[] = []
      let finalText = ''
      let errText = ''
      const t0 = Date.now()
      let firstPartialMs = 0
      const done = new Promise<void>((res) => {
        const asr = new AsrSession(
          {
            audioUrl: cfg.audioUrl,
            language: s.asrLanguage,
            provider: s.asrProvider,
            model: s.asrModel,
            sessionId: 'app-spike',
          },
          {
            onPartial: (t) => {
              if (!firstPartialMs) firstPartialMs = Date.now() - t0
              partials.push(t)
            },
            onFinal: (t) => {
              finalText = t
              res()
            },
            onError: (m) => {
              errText = m
              res()
            },
          },
          fakeRec,
        )
        void asr.start().then(async () => {
          // 按真实节奏喂（100ms/片）——一次性灌完会让 partial 全挤在一起，
          // 测不出「边说边上屏」这件事
          const step = 1600
          for (let i = 0; i < i16.length; i += step) {
            sink?.(i16.subarray(i, Math.min(i + step, i16.length)))
            await new Promise((r) => setTimeout(r, 100))
          }
          await asr.stop()
        })
      })
      await Promise.race([done, new Promise<void>((r) => setTimeout(r, 20000))])
      log('asr-inject: partial×' + partials.length + '（首个 @' + firstPartialMs + 'ms）' +
        (partials.length ? ' 末条「' + partials[partials.length - 1] + '」' : ''))
      log('asr-inject: final「' + finalText + '」' + (errText ? ' err=' + errText : ''))
      log('asr-inject: 原句「' + SAY + '」' +
        (finalText.replace(/[。？，！,.?!]/g, '') === SAY ? ' ✓ 逐字一致（忽略标点）' :
          finalText ? ' ≠' : ' ✗ 没识别到'))
    } catch (e: any) {
      log('asr-inject: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [log])

  // ── 流式 TTS：走真实 TtsSession（会话 start → 逐字 append → finish），
  //  量首音时延（验收判据「体感 <1.5s」的机器版）+ 同时录音测能量证明**真出声**。
  //  「我听不到」不是不能验证的理由——麦克风的能量读数就是客观证据。
  const probeTtsStream = useCallback(async () => {
    setBusy('tts')
    try {
      const cfg = await loadServerConfig()
      if (!cfg?.audioUrl) {
        log('tts: 未配置服务器')
        return
      }
      if (!(await ensurePermission())) return
      const s = settingsStore.getState().settings
      const SAY = '今天深圳晴，气温二十八度，适合出门。'
      let peak = 0
      const rec = recorder()
      await rec.start((frame) => {
        for (let i = 0; i < frame.length; i += 7) {
          const v = Math.abs(frame[i]) / 32768
          if (v > peak) peak = v
        }
      })
      const t0 = Date.now()
      let firstAudioMs = 0
      const sess = new TtsSession(
        { audioUrl: cfg.audioUrl, provider: s.ttsProvider, voice: s.voiceId },
        { onFirstAudio: () => { firstAudioMs = Date.now() - t0 } },
      )
      sess.start()
      // 逐字 append 模拟 speech_delta 的到达节奏（一次性 finish 测不出流式首音）
      for (const chp of SAY) {
        sess.append(chp)
        await new Promise((r) => setTimeout(r, 50))
      }
      const same = sess.finish(SAY)
      await Promise.race([sess.completion, new Promise<void>((r) => setTimeout(r, 25000))])
      await new Promise((r) => setTimeout(r, 300))
      await rec.stop()
      log('tts: 引擎=' + s.ttsProvider + '/' + s.voiceId +
        ' 首音=' + (firstAudioMs || -1) + 'ms（判据 <1500）' +
        ' 整段=' + (Date.now() - t0) + 'ms same=' + same)
      log('tts: 录到 peak=' + peak.toFixed(3) +
        '（底噪~0.06；高于它=确实出声了）')
    } catch (e: any) {
      log('tts: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [ensurePermission, log])

  // ── barge-in 物理面（M2-3 验收「播报中按 PTT 即停」的机器版）──
  //  播报中 stop() → 麦克风能量应当回到底噪。判据不是「代码调了 stop」而是
  //  **声音真的没了**：调了 stop 但排定的 source 继续播完，是 pcmPlayer 那类调度器的
  //  典型失败形态（stop 只清了队列没停已排定的）。
  const probeBargeIn = useCallback(async () => {
    setBusy('barge-in')
    try {
      const cfg = await loadServerConfig()
      if (!cfg?.audioUrl) {
        log('barge-in: 未配置服务器')
        return
      }
      if (!(await ensurePermission())) return
      const sc = speechController(cfg.audioUrl)
      const peaks = [0, 0, 0] // 0=播报中 1=stop 后 200ms 内（余音窗）2=stop 后稳态
      let phase = 0
      const rec = recorder()
      await rec.start((frame) => {
        for (let i = 0; i < frame.length; i += 7) {
          const v = Math.abs(frame[i]) / 32768
          if (v > peaks[phase]) peaks[phase] = v
        }
      })
      void sc.preview('今天深圳晴，气温二十八度，空气质量优，很适合出门散步或者去公园走走。')
      await new Promise((r) => setTimeout(r, 3200)) // 让它先播起来
      const during = peaks[0]
      sc.stop()
      phase = 1
      await new Promise((r) => setTimeout(r, 250)) // 余音窗：已排定的分片可能还响一下
      phase = 2
      await new Promise((r) => setTimeout(r, 1500))
      await rec.stop()
      log('barge-in: 播报中 peak=' + during.toFixed(3) +
        ' / stop 后 250ms=' + peaks[1].toFixed(3) +
        ' / 稳态=' + peaks[2].toFixed(3) + '（底噪~0.06）')
      log('barge-in: ' + (during > 0.09 && peaks[2] < during * 0.6
        ? '✓ 播报中有声、停后回落'
        : during <= 0.09 ? '? 播报期就没测到声音（先看 tts 流式那条）' : '✗ 停后仍有声'))
    } catch (e: any) {
      log('barge-in: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [ensurePermission, log])

  /** 让手机自己放一句话当声源（扬声器 → 空气 → 麦克风）。
   *  这不是偷懒：M2 的 barge-in 探针已经实证这条声学回路成立（播报中 mic peak 0.145、
   *  停后 0.004），所以**取证不必依赖「现场有人说话」**——而依赖人说话的探针
   *  没人在的时候就不能跑，跑出来的读数也不可复现。 */
  const speakForProbe = useCallback(
    async (text: string): Promise<boolean> => {
      const cfg = await loadServerConfig()
      if (!cfg?.audioUrl) {
        log('（无服务器配置，改成请你对着手机说话）')
        return false
      }
      void speechController(cfg.audioUrl).preview(text)
      return true
    },
    [log],
  )

  // ── M4-1 ⛔ VAD spike：ORT 在真机上能不能跑 silero，端点判据能不能出事件 ──
  //  取证要的是**概率分布**不只是事件：模型没跑起来（prob 恒 0）与「跑了但没人说话」
  //  在只看事件时长得一模一样。所以下面同时打 p50/p95/max 与 start/end 时刻。
  const probeVad = useCallback(async (ms = 12000) => {
    setBusy('vad')
    try {
      log('vad: native=' + vadNativeAvailable())
      if (!vadNativeAvailable()) return
      const vad = new VadEngine(800)
      const t0 = Date.now()
      await vad.load()
      log('vad: 模型载入 ' + (Date.now() - t0) + 'ms')
      const probs: number[] = []
      const events: string[] = []
      vad.onProb = (pr) => probs.push(pr)
      const tStart = Date.now()
      await vad.start({
        onSpeechStart: () => events.push('start@' + (Date.now() - tStart) + 'ms'),
        onSpeechEnd: () => events.push('end@' + (Date.now() - tStart) + 'ms'),
        onError: (m) => log('vad error: ' + m),
      })
      const mic = micLease()
      let frames = 0
      await mic.start((f) => {
        frames += 1
        vad.accept(f)
      })
      const spoke = await speakForProbe(
        '你好，我是小舟。今天深圳天气不错，二十八度，适合出门走走。',
      )
      log('vad: ' + (spoke ? '手机自播一句当声源' : '请对着手机说两句话') +
        '（' + Math.round(ms / 1000) + 's）…')
      await new Promise((r) => setTimeout(r, ms))
      await mic.stop()
      vad.stop()
      await vad.dispose()
      const sorted = [...probs].sort((a, b) => a - b)
      log(
        'vad: frames=' + frames + ' windows=' + probs.length +
        ' prob p50=' + (quantile(sorted, 0.5) ?? 0).toFixed(3) +
        ' p95=' + (quantile(sorted, 0.95) ?? 0).toFixed(3) +
        ' max=' + (sorted[sorted.length - 1] ?? 0).toFixed(3),
      )
      log('vad: events=' + (events.length ? events.join(' ') : '(无)'))
      log('vad: ' + (probs.length === 0 ? '✗ 一个窗都没推理（模型/帧源问题）'
        : (sorted[sorted.length - 1] ?? 0) < 0.5 ? '? 推理在跑但没有语音帧（说话了吗？麦克风被占？）'
        : events.some((e) => e.startsWith('start')) ? '✓ 概率有峰且判出端点' : '✗ 有语音概率但没判出端点'))
    } catch (e: any) {
      log('vad: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [log, speakForProbe])

  // ── M4-2 ⛔ KWS spike：说「小舟小舟」看有没有命中 ──
  const probeKws = useCallback(async (ms = 25000) => {
    setBusy('kws')
    try {
      log('kws: native=' + kwsNativeAvailable())
      if (!kwsNativeAvailable()) return
      // 原生 KeywordSpotter 是单例：免唤醒正开着的时候本探针抢不到它。**先说清楚**，
      // 别让「引擎被占用」以一条抛错的形式出现（那看起来像引擎坏了）。
      if (kwsBusy()) {
        log('kws: ✗ 原生 KWS 已被免唤醒回路占用——先在设置里关掉「免唤醒」再跑本探针')
        return
      }
      const kws = new KwsEngine()
      const t0 = Date.now()
      const hits: string[] = []
      await kws.start({ onKeyword: (k) => hits.push(k + '@' + (Date.now() - t0) + 'ms') }, DEFAULT_KEYWORDS)
      log('kws: 模型载入 ' + (Date.now() - t0) + 'ms')
      const mic = micLease()
      let fed = 0
      let refused = 0
      await mic.start((f) => {
        if (kws.accept(f)) fed += 1
        else refused += 1
      })
      // 自播三遍唤醒词（间隔留够，让 KWS 的解码状态在两次之间归零）
      const spoke = await speakForProbe('小舟小舟。')
      log('kws: ' + (spoke ? '手机自播「小舟小舟」' : '请说「小舟小舟」') +
        '（' + Math.round(ms / 1000) + 's，期间会再播两遍）…')
      await new Promise((r) => setTimeout(r, 7000))
      if (spoke) await speakForProbe('小舟小舟。')
      await new Promise((r) => setTimeout(r, 7000))
      if (spoke) await speakForProbe('小舟小舟。')
      await new Promise((r) => setTimeout(r, Math.max(2000, ms - 14000)))
      await mic.stop()
      const st = kws.stats()
      await kws.stop()
      log('kws: fed=' + fed + ' refused=' + refused + ' stats=' + JSON.stringify(st))
      log('kws: hits=' + (hits.length ? hits.join(' ') : '(无)'))
      log('kws: ' + (hits.length ? '✓ 唤醒命中 ' + hits.length + ' 次'
        : (st?.processed ?? 0) === 0 ? '✗ 原生一帧都没处理（载入/线程问题）'
        : '✗ 处理了 ' + st?.processed + ' 帧但零命中（阈值/关键词串/麦克风？）'))
    } catch (e: any) {
      log('kws: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [log, speakForProbe])

  // ── M4-2 KWS 直灌：绕过麦克风，用模型自带的测试音频喂引擎 ──
  //  **它是为了把「零命中」的两种成因分开**：引擎不认 vs 麦克风没听见——两者在屏上
  //  长得一模一样，而处置完全相反（改阈值/换模型 vs 调音量/靠近说）。
  //  用的是模型自己的 keywords（不是「小舟小舟」）：这一条验的是**引擎**，不是我们的词。
  const probeKwsInject = useCallback(async () => {
    setBusy('kws-inject')
    try {
      if (!kwsNativeAvailable()) {
        log('kws-inject: 原生不在场')
        return
      }
      if (kwsBusy()) {
        log('kws-inject: ✗ 原生 KWS 已被免唤醒回路占用——先在设置里关掉「免唤醒」再跑本探针')
        return
      }
      const { Asset } = require('expo-asset')
      // 模型自带 keywords.txt 的前四条（test_keywords.txt 与 test_wavs 一一对应）
      const testKeywords = [
        'w én s ēn t è k ǎ s uǒ @文森特卡索',
        'zh ōu w àng j ūn @周望军',
        'zh ū l ì n án @朱丽楠',
        'j iǎng y ǒu b ó @蒋友伯',
        'n ǚ ér @女儿',
        'f ǎ g uó @法国',
        'j iàn m iàn h uì @见面会',
        'l uò sh í @落实',
      ].join('/')
      const mods = [
        require('../../assets/models/kws_test/0.wav'),
        require('../../assets/models/kws_test/1.wav'),
        require('../../assets/models/kws_test/2.wav'),
        require('../../assets/models/kws_test/3.wav'),
        require('../../assets/models/kws_test/4.wav'),
        require('../../assets/models/kws_test/5.wav'),
        require('../../assets/models/kws_test/6.wav'),
      ]
      let total = 0
      for (let i = 0; i < mods.length; i += 1) {
        const kws = new KwsEngine()
        const hits: string[] = []
        await kws.start({ onKeyword: (k) => hits.push(k) }, testKeywords)
        const asset = Asset.fromModule(mods[i])
        await asset.downloadAsync()
        const uri: string = asset.localUri || asset.uri
        const bytes = new Uint8Array(await (await fetch(uri)).arrayBuffer())
        const wav = parseWav(bytes)
        if (!wav) {
          log('kws-inject: ' + i + '.wav 解析失败')
          await kws.stop()
          continue
        }
        const pcm = toMono(wav)
        // 按 100ms 分帧喂（与真实帧形态一致；一次性灌完会撑爆有界队列）
        for (let off = 0; off < pcm.length; off += 1600) {
          kws.accept(pcm.subarray(off, Math.min(off + 1600, pcm.length)))
          if (off % 16000 === 0) await new Promise((r) => setTimeout(r, 30))
        }
        await new Promise((r) => setTimeout(r, 1200)) // 等原生线程把队列跑完
        const st = kws.stats()
        await kws.stop()
        total += hits.length
        log('kws-inject: ' + i + '.wav rate=' + wav.sampleRate + ' samples=' + pcm.length +
          ' processed=' + st?.processed + ' dropped=' + st?.dropped +
          ' hits=' + (hits.length ? hits.join(',') : '(无)'))
      }
      log('kws-inject: ' + (total > 0
        ? '✓ 引擎能认（共 ' + total + ' 次命中）⇒ 麦克风那轮零命中是声学路径问题，不是引擎'
        : '✗ 直灌也零命中 ⇒ 问题在引擎/配置侧（阈值、关键词串格式、模型），与麦克风无关'))
    } catch (e: any) {
      log('kws-inject: 抛错 = ' + (e?.message ?? e))
    } finally {
      setBusy('')
    }
  }, [log])

  // ── M4-4 免唤醒整轮的无人取证：**只放一句话，不建任何引擎** ──
  //  用法：先在设置里打开免唤醒 → 回对话页确认「待唤醒」→ 进本屏点它 → 回对话页看状态条。
  //  它刻意**不自己建 KwsEngine**：原生侧的 KeywordSpotter 是单例，本按钮的全部作用
  //  就是「当一次声源」，听的人是常开麦那条回路——建第二个引擎会把要被取证的那个
  //  换掉，取证对象就不是免唤醒回路了。
  //  （2026-08-28 起抢占本身已被 kws.ts 的所有权守卫当场拦下并报错，不再静默串台；
  //   但「不该建」的理由与那道守卫无关，所以这段注释留着。）
  const probeSpeakWake = useCallback(async () => {
    setBusy('speak-wake')
    try {
      const ok = await speakForProbe('小舟小舟。今天深圳天气怎么样？')
      log('speak-wake: ' + (ok ? '已播「小舟小舟。今天深圳天气怎么样？」——回对话页看状态条'
        : '无服务器配置，播不了'))
      await new Promise((r) => setTimeout(r, 6000))
    } finally {
      setBusy('')
    }
  }, [log, speakForProbe])

  // ── M4 可用性与音频焦点日志（M3 遗留 R2 的取证出口）──
  const probeStatus = useCallback(() => {
    const a = handsFreeAvailability()
    log('avail: vad=' + a.vad + ' kws=' + a.kws + ' usable=' + a.usable + ' mic=' + JSON.stringify(micBusStats()))
    log('focus: installed=' + audioFocusInstalled() + '（false ⇒ 四场景一个都不会到，先查这一位）')
    const lg = audioFocusLog()
    if (!lg.length) log('focus: 日志空——还没发生过中断/路由事件')
    for (const e of lg) {
      log('focus: ' + new Date(e.at).toLocaleTimeString() + ' ' + e.kind + ' ' + e.detail + ' stopped=' + e.stoppedPlayback)
    }
  }, [log])

  const Btn = ({ label, onPress }: { label: string; onPress: () => void }) => (
    <Pressable
      onPress={onPress}
      disabled={!!busy}
      style={{
        backgroundColor: busy ? p.line : p.accent,
        borderRadius: 10,
        paddingHorizontal: 12,
        paddingVertical: 9,
      }}
    >
      <Text style={{ color: '#fff', fontSize: p.font(13) }}>{label}</Text>
    </Pressable>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: p.bg }} edges={['top', 'bottom']}>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, padding: 10 }}>
        <Btn label="A rec5s" onPress={() => void probeA()} />
        <Btn label="B rec5s" onPress={() => void probeB()} />
        <Btn label="B 3min" onPress={() => void probeB(180_000)} />
        <Btn label="inject" onPress={() => void probeInject()} />
        <Btn label="pcmPlayer" onPress={() => void probePlayer()} />
        <Btn label="coexist" onPress={() => void probeCoexist()} />
        <Btn label="e2e 回环" onPress={() => void probeLoopback()} />
        <Btn label="asr 直灌" onPress={() => void probeAsrInject()} />
        <Btn label="tts 流式" onPress={() => void probeTtsStream()} />
        <Btn label="barge-in" onPress={() => void probeBargeIn()} />
        <Btn label="M4 vad" onPress={() => void probeVad()} />
        <Btn label="M4 kws" onPress={() => void probeKws()} />
        <Btn label="M4 kws 直灌" onPress={() => void probeKwsInject()} />
        <Btn label="M4 播唤醒句" onPress={() => void probeSpeakWake()} />
        <Btn label="M4 状态/焦点" onPress={() => probeStatus()} />
        <Btn label="clear" onPress={() => setLines([])} />
      </View>
      <Text style={{ color: p.fg3, fontSize: p.font(12), paddingHorizontal: 12 }}>
        {busy ? 'running: ' + busy : 'idle'}
      </Text>
      <ScrollView style={{ flex: 1, padding: 12 }}>
        {lines.map((l, i) => (
          <Text
            key={i + '-' + l.slice(0, 12)}
            selectable
            style={{ color: p.fg1, fontSize: p.font(11), marginBottom: 6 }}
          >
            {l}
          </Text>
        ))}
      </ScrollView>
    </SafeAreaView>
  )
}
