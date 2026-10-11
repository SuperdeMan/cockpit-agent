// Serve only the completed browser build. No source transforms, HMR or arbitrary files.
import { createReadStream } from 'node:fs'
import { readdir, stat } from 'node:fs/promises'
import { extname, join, resolve } from 'node:path'
import { pipeline } from 'node:stream'
import { createGzip } from 'node:zlib'
import { createMapMiddleware } from './amap.mjs'

export const ISOLATION_HEADERS = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'credentialless',
  'X-Content-Type-Options': 'nosniff',
}
const MIME = {
  '.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8',
  '.mjs':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8',
  '.json':'application/json; charset=utf-8', '.svg':'image/svg+xml',
  '.txt':'text/plain; charset=utf-8', '.wasm':'application/wasm',
  '.woff2':'font/woff2', '.woff':'font/woff', '.png':'image/png',
  '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp',
  '.ico':'image/x-icon', '.onnx':'application/octet-stream', '.data':'application/octet-stream',
}
const COMPRESS = new Set(['.html','.js','.mjs','.css','.json','.svg','.txt','.wasm','.onnx','.data'])

export function acceptsGzip(header = '') {
  const options = String(header).toLowerCase().split(',').map(item => {
    const [name, ...params] = item.trim().split(';')
    const q = params.map(p => p.trim()).find(p => p.startsWith('q='))
    return {name, q:q ? Number(q.slice(2)) : 1}
  })
  return (options.find(o => o.name === 'gzip') || options.find(o => o.name === '*'))?.q > 0
}

function reject(res, status) {
  res.writeHead(status, {'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'})
  res.end(status === 404 ? 'Not found' : 'Request rejected')
}

export async function createStaticHandler({ root, env = process.env, request, allowedHosts = [] }) {
  const directory = resolve(root)
  const files = new Map()
  async function collect(dir, prefix = '') {
    for (const entry of await readdir(dir, {withFileTypes:true})) {
      if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue
      const name = prefix + '/' + entry.name, path = join(dir, entry.name)
      if (entry.isDirectory()) await collect(path, name)
      else if (entry.isFile()) {
        const extension = extname(entry.name).toLowerCase()
        if (!MIME[extension]) continue
        const info = await stat(path)
        files.set(name, {path, extension, size:info.size,
          etag:`W/"${info.size.toString(16)}-${Math.trunc(info.mtimeMs).toString(16)}"`})
      }
    }
  }
  await collect(directory)
  if (!files.has('/index.html')) throw new Error('HMI build has no index.html')
  const hosts = new Set(['localhost','127.0.0.1','[::1]', ...allowedHosts].map(h => h.toLowerCase()))
  const maps = createMapMiddleware({env, ...(request ? {request} : {})})

  return (req, res) => {
    for (const [name,value] of Object.entries(ISOLATION_HEADERS)) res.setHeader(name,value)
    let pathname
    try {
      const authority = new URL('http://' + (req.headers.host || ''))
      if (authority.username || authority.password || authority.pathname !== '/' || authority.search || authority.hash
        || !hosts.has(authority.hostname.toLowerCase())) return reject(res,403)
      if (!req.url?.startsWith('/')) return reject(res,400)
      const url = new URL(req.url,'http://hmi.invalid')
      if (url.origin !== 'http://hmi.invalid') return reject(res,400)
      pathname = decodeURIComponent(url.pathname)
    } catch { return reject(res,400) }
    maps(req,res,() => {
      if (!['GET','HEAD'].includes(req.method)) return reject(res,405)
      const file = files.get(pathname === '/' ? '/index.html' : pathname)
      if (!file) return reject(res,404)
      const hashed = /^\/assets\/.+-[\w-]{8}\.[\w]+$/.test(pathname)
      res.setHeader('Content-Type',MIME[file.extension])
      res.setHeader('Cache-Control',file.extension === '.html' ? 'no-store'
        : hashed ? 'private, max-age=31536000, immutable' : 'private, max-age=0, must-revalidate')
      res.setHeader('ETag',file.etag)
      res.setHeader('Vary','Accept-Encoding')
      const gzip = file.size >= 1024 && COMPRESS.has(file.extension) && acceptsGzip(req.headers['accept-encoding'])
      if (gzip) res.setHeader('Content-Encoding','gzip')
      if (req.headers['if-none-match']?.split(/\s*,\s*/).some(tag => tag === file.etag || tag === '*')) {
        res.writeHead(304);res.end();return
      }
      if (!gzip) res.setHeader('Content-Length',file.size)
      if (req.method === 'HEAD') { res.end();return }
      // Stream large voice assets; do not buffer models or keep a second in-memory cache.
      const input = createReadStream(file.path)
      const done = error => { if (error && !res.destroyed) res.destroy() }
      if (gzip) pipeline(input,createGzip(),res,done)
      else pipeline(input,res,done)
    })
  }
}
