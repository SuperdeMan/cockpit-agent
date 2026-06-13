// 顶部状态栏：助手标识 + 连接/模型状态 + 播报开关 + 设置入口。
import { useSettings } from '../settings'

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
    <header className="statusbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          <span className="ring" />
          <span className="core" />
        </span>
        <span className="brand-name">{settings.assistantName}</span>
        <span className={'conn ' + (connected ? 'online' : 'offline')}>
          <span className="conn-dot" />
          {connected ? '在线' : '连接中'}
          <span className="conn-sep">·</span>
          <span className="conn-model">{MODEL_LABEL[settings.model]}</span>
        </span>
      </div>
      <div className="statusbar-actions">
        <button
          className={'icon-btn audio ' + (settings.ttsEnabled ? 'on' : 'off')}
          onClick={() => update({ ttsEnabled: !settings.ttsEnabled })}
          title={settings.ttsEnabled ? '关闭语音播报' : '开启语音播报'}
          aria-label="语音播报开关"
        >
          {settings.ttsEnabled ? '🔊' : '🔇'}
        </button>
        <button className="icon-btn" onClick={onOpenSettings} title="设置" aria-label="打开设置">
          ⚙
        </button>
      </div>
    </header>
  )
}
