export type Theme = 'dark' | 'light' | 'system'
export type Size = 'workbench' | 'presentation'

export function preference(key: string, fallback: string) {
  try { return localStorage.getItem('obs-' + key) || fallback } catch { return fallback }
}

export function savePreference(key: string, value: string) {
  try { localStorage.setItem('obs-' + key, value) } catch { /* private browsing: this page still works */ }
}

export function initialTheme(): Theme {
  const value = new URLSearchParams(location.search).get('theme') || preference('theme', 'system')
  return value === 'dark' || value === 'light' ? value : 'system'
}

export function initialSize(): Size {
  return (new URLSearchParams(location.search).get('size') || preference('size', 'workbench')) === 'presentation' ? 'presentation' : 'workbench'
}

export function applyPreferences(theme: Theme, size: Size) {
  document.documentElement.dataset.theme = theme === 'system'
    ? (window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : theme
  document.documentElement.dataset.size = size
}
