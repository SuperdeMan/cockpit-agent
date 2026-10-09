import { useEffect, useState } from 'react'

export type ViewKey = 'turns' | 'live' | 'logs' | 'llm' | 'badcases'
const VIEWS = new Set(['turns', 'live', 'logs', 'llm', 'badcases'])
export type Route = { view: ViewKey; trace: string; query: string }

export function readRoute(): Route {
  const params = new URLSearchParams(window.location.search)
  const view = params.get('view') || 'turns'
  return { view: VIEWS.has(view) ? view as ViewKey : 'turns', trace: params.get('trace') || '', query: params.get('q') || '' }
}

export function navigate(patch: Partial<Route>, replace = false) {
  const next = { ...readRoute(), ...patch }
  const url = new URL(window.location.href)
  for (const [key, value] of Object.entries({ view: next.view, trace: next.trace, q: next.query })) {
    if (value) url.searchParams.set(key, value)
    else url.searchParams.delete(key)
  }
  window.history[replace ? 'replaceState' : 'pushState'](null, '', url)
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function useRoute() {
  const [route, setRoute] = useState(readRoute)
  useEffect(() => {
    const changed = () => setRoute(readRoute())
    window.addEventListener('popstate', changed)
    return () => window.removeEventListener('popstate', changed)
  }, [])
  return route
}

// Copy only documented UI state, never arbitrary query parameters or credentials.
export function shareableUrl() {
  const current = new URL(window.location.href)
  const url = new URL(current.pathname, current.origin)
  for (const key of ['view', 'trace', 'q', 'fixture', 'theme', 'size']) {
    const value = current.searchParams.get(key)
    if (value) url.searchParams.set(key, value)
  }
  return url.href
}
