// 音频面两条流式端点的 URL 规则——唯一声明源。asr.ts / tts.ts 从这里再导出（调用方 import 路径不变），
// speech.ts / handsFree.ts / usePtt / AssistantProvider 预热连接时直接读这里：
// 它们不该为了拿一个 URL 去依赖整个会话模块（jest 里那两个模块常被整体 mock，mock 不会带这个函数）。
export function asrStreamUrl(audioUrl: string): string {
  return audioUrl.replace(/^http/, 'ws') + '/api/asr/stream'
}

export function ttsStreamUrl(audioUrl: string): string {
  return audioUrl.replace(/^http/, 'ws') + '/api/tts/stream'
}
