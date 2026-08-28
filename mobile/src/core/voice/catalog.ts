// 语音引擎目录探测（实施计划 M2-2/M2-3 设置页两级选择的数据源）。
//
// 两个端点都**可以失败**（没配 key / 网关不可达 / 离线打开设置页），失败时的形态刻意不同：
//  · TTS：回落共享契约里的 `TTS_PROVIDER_FALLBACK`——用户仍能选引擎和音色，
//    代价小于「设置页一片空白」。
//    ⚠ 这里原来写的是「选错了播的时候会无感回退批处理」——**那是假的**（2026-08-28
//    查实，见 tts.ts 头注的四段链）：批处理用的是同一个引擎，无 key 时返回零字节。
//    所以「让用户能选一个不可用的引擎」这件事本身没问题，**但选完必须听得见反馈**
//    ——试听按钮会明说没出声，播报静默也有出口（speech.ts::onSilent）。
//    另：`available` 只判 env 里那个 key 字符串**非空**，判不出「key 在但已失效」
//    （云栈 MiMo 就是这个状态）⇒ 靠「（未配置）」角标是挡不住这类的，只能靠事后反馈。
//  · ASR：回落一张最小静态表。**不回落成空**，同理。
import { TTS_PROVIDER_FALLBACK, type TtsProviderInfo } from '@shared/types.ts'

export interface AsrProviderInfo {
  id: string
  label: string
  available: boolean
  models: string[]
}

/** ASR 引擎的静态兜底表（与 `llm-gateway/http_server.py:292-306` 的 id/label 同步） */
export const ASR_PROVIDER_FALLBACK: AsrProviderInfo[] = [
  // 模型顺序 = 偏好顺序（泓舟 2026-08-26：fun-asr 主、qwen3-asr 次）
  { id: 'dashscope', label: 'DashScope 实时', available: true, models: ['fun-asr-realtime', 'qwen3-asr-flash-realtime-2026-02-10'] },
  { id: 'mimo', label: 'MiMo 分块', available: true, models: ['mimo-v2.5-asr'] },
]

async function getJson(url: string, timeoutMs = 6000): Promise<unknown> {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  try {
    const resp = await fetch(url, { signal: ctl.signal })
    return await resp.json()
  } finally {
    clearTimeout(timer)
  }
}

export async function fetchTtsProviders(audioUrl: string): Promise<TtsProviderInfo[]> {
  try {
    const data = (await getJson(audioUrl + '/api/tts/stream/info')) as { providers?: unknown }
    if (Array.isArray(data.providers) && data.providers.length) {
      return data.providers as TtsProviderInfo[]
    }
  } catch {
    /* 落回下面的静态表 */
  }
  return TTS_PROVIDER_FALLBACK
}

export async function fetchAsrProviders(audioUrl: string): Promise<AsrProviderInfo[]> {
  try {
    const data = (await getJson(audioUrl + '/api/asr/stream/info')) as { providers?: unknown }
    if (Array.isArray(data.providers) && data.providers.length) {
      // ⚠ 这个端点返回的 model id 是**展示用的 CamelCase**
      // （'Qwen3-ASR-Flash-Realtime-…'），而流式 start 帧只认全小写——大写会 1011
      // 断连（types.ts:931 的原账）。在**入口**就归一，别指望每个消费方记得。
      return (data.providers as AsrProviderInfo[]).map((p) => ({
        ...p,
        models: (p.models || []).map((m) => String(m).toLowerCase()),
      }))
    }
  } catch {
    /* 落回下面的静态表 */
  }
  return ASR_PROVIDER_FALLBACK
}
