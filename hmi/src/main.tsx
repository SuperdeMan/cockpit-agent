import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { SettingsProvider } from './settings'
import './styles.css'
import './aurora.css' // Aurora Glass 设计系统层（P0，--au-* 与旧 token 并存，非破坏）
import './shell.css' // Aurora Glass 应用外壳（P1，两栏布局 + 右舞台）
import { AuroraPreview } from './components/aurora/AuroraPreview'
import { DEMO_WEATHER, DEMO_MAP } from './demo'

// ?aurora 进入 P0 设计系统预览；?demo / ?demo=map 用 mock 对话验证 P1 场景；否则正式应用。
const params = new URLSearchParams(typeof window !== 'undefined' ? window.location.search : '')
const showAurora = params.has('aurora')
const seedMessages = params.has('demo')
  ? params.get('demo') === 'map'
    ? DEMO_MAP
    : DEMO_WEATHER
  : undefined

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {showAurora ? (
      <AuroraPreview />
    ) : (
      <SettingsProvider>
        <App seedMessages={seedMessages} />
      </SettingsProvider>
    )}
  </React.StrictMode>,
)
