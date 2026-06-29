// 顶部状态栏（Aurora Glass）：小舟光球 + 助手名 + 连接/模型态 + 播报开关 + 设置入口。
import { useSettings } from '../settings'
import { AuroraOrb } from './aurora'

const MODEL_LABEL: Record<string, string> = { fast: '快速', deep: '深度推理', auto: '自动' }

export function StatusBar({
  connected,
  onOpenSettings,
}: {
  connected: boolean
  onOpenSettings: () => void
}) {
  const { settings, update } = useSettings()
  return (
    <header className="au-statusbar">
      <div className="au-sb-brand">
        <AuroraOrb size={30} state="idle" />
        <span className="au-sb-name">{settings.assistantName}</span>
        <span className={'au-conn' + (connected ? '' : ' offline')}>
          <span className="au-conn-dot" />
          {connected ? '在线' : '连接中'}
        </span>
        <span className="au-pill">{MODEL_LABEL[settings.model]}</span>
      </div>
      <div className="au-sb-actions">
        <button
          className={'au-icon-btn' + (settings.ttsEnabled ? ' on' : '')}
          onClick={() => update({ ttsEnabled: !settings.ttsEnabled })}
          title={settings.ttsEnabled ? '关闭语音播报' : '开启语音播报'}
          aria-label="语音播报开关"
        >
          {settings.ttsEnabled ? '🔊' : '🔇'}
        </button>
        <button className="au-icon-btn" onClick={onOpenSettings} title="设置" aria-label="打开设置">
          ⚙
        </button>
      </div>
    </header>
  )
}
