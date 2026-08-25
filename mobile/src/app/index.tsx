// 首页 = 对话主屏（M1-3 起）。无配置时 ChatScreen 内部 Redirect 到 /onboarding。
import { ChatScreen } from '@/features/chat/ChatScreen'

export default function Home() {
  return <ChatScreen />
}
