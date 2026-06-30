// 顶部状态栏（Aurora Glass，照 A-2）：小舟光球 + 名 + 在线 + 模型态 | 日期 + 仪表时钟 + 播报 + 设置。
import { useEffect, useState } from 'react'
import { useSettings } from '../settings'
import { AuroraOrb } from './aurora'
import { Icon } from './Icon'

const MODEL_LABEL: Record<string, string> = { fast: '快速', deep: '深度推理', auto: '自动' }
const WEEK = '日一二三四五六'

export function StatusBar({
  connected,
  onOpenSettings,
}: {
  connected: boolean
  onOpenSettings: () => void
}) {
  const { settings, update } = useSettings()
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000 * 15)
    return () => clearInterval(t)
  }, [])
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const date = `周${WEEK[now.getDay()]} · ${now.getMonth() + 1}月${now.getDate()}日`

  return (
    <header className="au-statusbar">
      <div className="au-sb-brand">
        <AuroraOrb size={30} state="idle" />
        <span className="au-sb-name">{settings.assistantName}</span>
        <span className="au-sb-divider" />
        <span className={'au-conn' + (connected ? '' : ' offline')}>
          <span className="au-conn-dot" />
          {connected ? '在线' : '连接中'}
        </span>
        <span className="au-pill">{MODEL_LABEL[settings.model]}</span>
      </div>
      <div className="au-sb-actions">
        <span className="au-sb-date">{date}</span>
        <span className="au-num au-sb-clock">{hh}:{mm}</span>
        <span className="au-sb-divider" />
        <button
          className={'au-icon-btn' + (settings.ttsEnabled ? ' on' : '')}
          onClick={() => update({ ttsEnabled: !settings.ttsEnabled })}
          title={settings.ttsEnabled ? '关闭语音播报' : '开启语音播报'}
          aria-label="语音播报开关"
        >
          <Icon name="voice-output" size={18} state={settings.ttsEnabled ? 'active' : 'default'} />
        </button>
        <button className="au-icon-btn" onClick={onOpenSettings} title="设置" aria-label="打开设置">
          <Icon name="settings" size={18} />
        </button>
      </div>
    </header>
  )
}
