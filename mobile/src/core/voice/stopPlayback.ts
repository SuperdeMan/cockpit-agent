// 「只停播」的合成出口（AR03 / 评审 R06）。
//
// 停播要同时管两路声音：主链 TTS 在 `SpeechController`，S2S 自答在 `HandsFreeController`
// 手里的 `S2SClient`。**顺序本身就是判据**，所以它有名字、有测试，而不是散在组件里的两行。
//
// 反过来先停 `SpeechController` 会失效，且失效得很安静：它的收尾会同步回调
// `onSpeechEnded → HandsFreeController.ttsEnd() → voiceLoop.ttsEnd()`，把 FSM 从 SPEAKING
// 推进 **FOLLOWUP**（8s 免唤醒续问窗，下一句不用唤醒词就上行）；随后的 `stopSpeaking()`
// 因为已经不在 SPEAKING/THINKING 而变成空操作——屏上声音停了，采集窗却开着，
// 正是 R06「停止不得隐式开启麦克风」要修的那件事。
//
// 先走免唤醒这一路：它在「用户停播」标志内调同一个停播出口，`ttsEnd()` 被短路，
// FSM 直接收到 ARMED。免唤醒关着（或原生缺席）时它是 no-op，主链停播落在第二步；
// `stop()` 幂等，两条路都走到时第二次只是把 speaking 再落一次。
export interface StopPlaybackDeps {
  handsFree: { stopSpeaking(): void }
  speech: { stop(): void }
}

export function stopPlayback(deps: StopPlaybackDeps): void {
  deps.handsFree.stopSpeaking()
  deps.speech.stop()
}

/** 停播键此刻该不该给（AR03 / 评审 R06「播放器缓冲阶段」）。
 *
 *  `busy`（pending/streaming/processActive）只说「云端这一轮还没落地」，那时该给的是「打断」。
 *  真的在出声当然要给停播。剩下的那一格才是坑：**全文一次 `final` 到达之后、首片音频起播之前**
 *  ——三个忙态同帧清零、声音又还没出来，只看这两个量的话屏上一个停播入口都没有，而播放器
 *  已经在合成、马上就要出声。`live` 补的正是这一格。 */
export function canStopPlayback(i: { playing: boolean; live: boolean; busy: boolean }): boolean {
  return i.playing || (i.live && !i.busy)
}
