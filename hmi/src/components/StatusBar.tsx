// 顶部状态栏（Aurora Glass，照 A-2）：小舟光球 + 名 + 在线 + 模型态 | 日期 + 仪表时钟 + 播报 + 设置。
import { useEffect, useState } from 'react'
import { useSettings } from '../settings'
import { Icon } from './Icon'
import { useDriving } from '../DrivingContext'

const WEEK = '日一二三四五六'

export function StatusBar({
  connection,
  onOpenSettings,
  privacyMic = false, privacyCloud = false, cameraFrameAt = 0,
}: {
  connection: string
  onOpenSettings: () => void
  privacyMic?: boolean
  privacyCloud?: boolean
  cameraFrameAt?: number
}) {
  const { settings, update } = useSettings()
  const { driving, setDriving } = useDriving()
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  const hh = String(now.getHours()).padStart(2, '0')
  const mm = String(now.getMinutes()).padStart(2, '0')
  const date = `周${WEEK[now.getDay()]} · ${now.getMonth() + 1}月${now.getDate()}日`
  const privacyCamera = cameraFrameAt > 0 && now.getTime() - cameraFrameAt < 10_000
  const connectionLabel = connection === 'open' ? '已连接' : connection === 'connecting' ? '连接中' : '连接已断开'

  return (
    <header className="au-statusbar">
      <div className="au-sb-brand">
        <span className="au-sb-name">{settings.assistantName}</span>
        <span className={'au-conn ' + connection} role="status" aria-label={connectionLabel} title={connectionLabel}>
          <span className="au-conn-dot" />
          {connection !== 'open' && <span>{connectionLabel}</span>}
        </span>
      </div>
      <div className="au-sb-actions">
        {(privacyMic || privacyCloud || privacyCamera) && <span className="au-privacy">
          {privacyMic && <Icon name="privacy-mic" size={28} color="var(--au-warn)" title="麦克风已启用" />}
          {privacyCloud && <Icon name="privacy-cloud" size={28} color="var(--au-warn)" title="本次语音使用云端识别" />}
          {privacyCamera && <Icon name="privacy-camera" size={28} color="var(--au-warn)" title="刚拍摄了一帧" />}
        </span>}
        {driving && <button className="au-driving-toggle" onClick={() => setDriving(false)} title="退出当前行车段">行车中</button>}
        <span className="au-num au-sb-clock" title={date}>{hh}:{mm}</span>
        <button
          className={'au-icon-btn' + (settings.ttsEnabled ? ' on' : '')}
          onClick={() => update({ ttsEnabled: !settings.ttsEnabled })}
          title={settings.ttsEnabled ? '关闭语音播报' : '开启语音播报'}
          aria-label="语音播报开关"
        >
          <Icon name="voice-output" size={28} state={settings.ttsEnabled ? 'active' : 'default'} />
        </button>
        <button className="au-icon-btn" onClick={onOpenSettings} title="设置" aria-label="打开设置">
          <Icon name="settings" size={28} />
        </button>
      </div>
    </header>
  )
}
