import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { SettingsProvider } from './settings'
import './styles.css'
import './aurora.css' // Aurora Glass 设计系统层（P0，--au-* 与旧 token 并存，非破坏）
import './shell.css' // Aurora Glass 应用外壳（P1，两栏布局 + 右舞台）
import './cards.css' // Aurora Glass 卡片重皮（P2，覆盖 Cards.tsx 既有语义类）
import { AuroraPreview } from './components/aurora/AuroraPreview'
import { DEMO_WEATHER, DEMO_MAP, DEMO_CARDS, DEMO_STATES, DEMO_INFO, DEMO_CHARGE, DEMO_TRIP, DEMO_ROUTE } from './demo'

const DEMO_MAPS: Record<string, typeof DEMO_WEATHER> = { charge: DEMO_CHARGE, trip: DEMO_TRIP, route: DEMO_ROUTE }

// ?aurora 进入 P0 设计系统预览；?demo / ?demo=map / =cards / =states 用 mock 对话验证；否则正式应用。
const params = new URLSearchParams(typeof window !== 'undefined' ? window.location.search : '')
const showAurora = params.has('aurora')
const demoParam = params.get('demo')
const seedMessages = params.has('demo')
  ? demoParam === 'map'
    ? DEMO_MAP
    : demoParam === 'cards'
      ? DEMO_CARDS
      : demoParam === 'states'
        ? DEMO_STATES
        : demoParam === 'info'
          ? DEMO_INFO
          : (demoParam && DEMO_MAPS[demoParam]) || DEMO_WEATHER
  : undefined

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {showAurora ? (
      <AuroraPreview />
    ) : (
      <SettingsProvider>
        <App seedMessages={seedMessages} openSettings={params.has('settings')} />
      </SettingsProvider>
    )}
  </React.StrictMode>,
)
