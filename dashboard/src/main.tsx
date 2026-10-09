import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { applyPreferences, initialSize, initialTheme } from './preferences'
import './tokens.css'
import './styles.css'

applyPreferences(initialTheme(), initialSize())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
