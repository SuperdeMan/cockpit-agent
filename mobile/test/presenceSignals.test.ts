// B1-5：Presence 的四路输入信号必须是**可订阅的结构化事实**，不是回调链上的字符串。
// 此前 `speechController` 的 onSpeechBegan/Ended 只能被一个消费方链式覆盖（useHandsFree 就是
// 这么接的），第二个消费方要么覆盖掉第一个、要么复制那套「存 prev 再链回去」的脆弱写法。
import { registerVisionCapturer, subscribeVisionCapturing, captureVisionFrame } from '@/core/vision/frame'
import { SpeechController } from '@/core/voice/speech'

// 播放器与 TTS 会话在 jest 里不存在：只订阅状态，不真播
jest.mock('@/core/voice/audioCtx', () => ({ newPcmPlayer: jest.fn(() => ({ push() {}, remainingSec: () => 0, stop() {} })) }))

describe('speech.subscribeSpeaking', () => {
  test('首片音频 → true；stop → false；退订后不再收', () => {
    const sc = new SpeechController('https://x')
    const seen: boolean[] = []
    const off = sc.subscribeSpeaking((v) => seen.push(v))
    ;(sc as unknown as { setSpeaking(v: boolean): void }).setSpeaking(true)
    sc.stop()
    off()
    ;(sc as unknown as { setSpeaking(v: boolean): void }).setSpeaking(true)
    expect(seen).toEqual([true, false])
    expect(sc.speaking).toBe(true)
  })
})

describe('vision.subscribeVisionCapturing', () => {
  test('抓帧期间 true，结束（含失败）后 false', async () => {
    const seen: boolean[] = []
    const off = subscribeVisionCapturing((v) => seen.push(v))
    let release: () => void = () => {}
    registerVisionCapturer(() => new Promise<string>((r) => (release = () => r(''))))
    const p = captureVisionFrame('https://audio')
    await Promise.resolve()
    expect(seen).toEqual([true])
    release()
    await p
    expect(seen).toEqual([true, false])
    off()
    registerVisionCapturer(null)
  })
})
