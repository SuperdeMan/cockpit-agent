// Server-only JS API configuration and a bounded map-auth/style proxy.
// Credentials come from the selected runtime environment, never from VITE_*.
import https from 'node:https'

const PREFIX = '/_AMapService/'
const MAX_BODY = 2 * 1024 * 1024

export function mapCredentials(env = process.env) {
  const key = (env.AMAP_JS_KEY || '').trim()
  const code = (env.AMAP_JS_SECURITY_CODE || '').trim()
  return /^[a-zA-Z0-9_-]{16,128}$/.test(key) && /^[a-zA-Z0-9_-]{16,128}$/.test(code) ? { key, code } : null
}

export function mapUpstream(raw, credentials) {
  if (!credentials || typeof raw !== 'string' || raw.length > 16384 || !raw.startsWith(PREFIX)) return null
  const input = new URL(raw, 'http://hmi.invalid')
  const path = input.pathname.slice(PREFIX.length)
  // No search, driving, geocoding, arbitrary URL or merchant API forwarding.
  const style = path === 'v4/map/styles' || path === 'v4/map/styles/info'
  if (!style && path !== 'v3/log/init') return null
  if (input.searchParams.getAll('key').length !== 1 || input.searchParams.get('key') !== credentials.key) return null
  const target = new URL('/' + path, style ? 'https://webapi.amap.com' : 'https://restapi.amap.com')
  target.search = input.search
  target.searchParams.delete('jscode')
  target.searchParams.set('jscode', credentials.code)
  return target
}

function reply(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' })
  res.end(JSON.stringify(body))
}

export function createMapMiddleware({ env = process.env, request = https.request } = {}) {
  return (req, res, next) => {
    const raw = req.url || ''
    if (raw.split('?')[0] !== '/api/maps/config' && !raw.startsWith('/_AMapService')) return next()
    if (req.method !== 'GET') return reply(res, 405, { error: 'method_not_allowed' })
    // Browsers must call this service from the HMI origin. No CORS grants.
    const site = req.headers['sec-fetch-site']
    if (site && !['same-origin','none'].includes(site)) return reply(res, 403, { error: 'map_request_rejected' })
    for (const header of ['origin','referer']) {
      if (!req.headers[header]) continue
      try {
        if (new URL(req.headers[header]).host !== req.headers.host) return reply(res, 403, { error: 'map_request_rejected' })
      } catch { return reply(res, 403, { error: 'map_request_rejected' }) }
    }
    const credentials = mapCredentials(env)
    if (raw.split('?')[0] === '/api/maps/config') {
      // JS API's public application key must reach the browser. The paired code must not.
      return reply(res, 200, credentials ? { available: true, key: credentials.key } : { available: false })
    }
    if (!credentials) return reply(res, 503, { error: 'map_unavailable' })
    let target
    try { target = mapUpstream(raw, credentials) } catch { /* malformed request */ }
    if (!target) return reply(res, 403, { error: 'map_request_rejected' })
    let settled = false
    const fail = () => {
      if (settled || res.destroyed) return
      settled = true
      reply(res, 502, { error: 'map_upstream_unavailable' })
    }
    // Never log upstream URLs/errors: their query contains the security code.
    try {
      const upstream = request(target, { method: 'GET', headers: {
        Accept: '*/*',
        ...(req.headers.referer ? { Referer: req.headers.referer } : {}),
      } }, response => {
        if (response.statusCode !== 200) { response.resume(); fail(); return }
        const chunks = []
        let size = 0
        response.on('data', chunk => {
          size += chunk.length
          if (size > MAX_BODY) { response.destroy(); fail() } else chunks.push(chunk)
        })
        response.on('error', fail)
        response.on('end', () => {
          if (settled || res.destroyed) return
          const body = Buffer.concat(chunks)
          // Upstream diagnostics must never reflect our server-side secret.
          if (body.includes(credentials.code)) { fail(); return }
          settled = true
          res.writeHead(200, { 'Content-Type': response.headers['content-type'] || 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' })
          res.end(body)
        })
      })
      upstream.setTimeout(10000, () => { upstream.destroy(); fail() })
      upstream.on('error', fail)
      res.on('close', () => upstream.destroy())
      upstream.end()
    } catch { fail() }
  }
}

export function amapPlugin() {
  const install = server => { server.middlewares.use(createMapMiddleware()) }
  return { name: 'hmi-amap-runtime', configureServer: install, configurePreviewServer: install }
}
