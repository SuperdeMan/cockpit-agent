// xiaozhou://voice 的落点（方案 §9「语音层可由 deeplink 直接升起」+ §12.2 红线；B5-8）：
// 只回对话页并升起语音层，**不开麦**——进入后仍需一次轻点 / 长按才录音。升层由 ChatScreen 消费 voice=1。
import { Redirect } from 'expo-router'

export default function VoiceDeeplink() {
  return <Redirect href={{ pathname: '/', params: { voice: '1' } }} />
}
