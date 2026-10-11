// Build with the existing runtime VITE_* values, then open the HTTP listener.
// Credentials are never Docker build args; AMAP_JS_SECURITY_CODE remains server-only.
import { createServer } from 'node:http'
import { resolve, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createStaticHandler } from './static.mjs'

const HMI_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

export function parseServeArgs(args) {
  const options = {host:'127.0.0.1',port:5173,configFile:undefined}
  for (let i=0;i<args.length;i+=2) {
    const key=args[i],value=args[i+1]
    if (!value || !['--host','--port','--config'].includes(key)) throw new Error('Invalid HMI serve option')
    if (key==='--host') options.host=value
    if (key==='--config') options.configFile=value
    if (key==='--port') {
      if (!/^\d+$/.test(value) || Number(value)<1 || Number(value)>65535) throw new Error('Invalid HMI port')
      options.port=Number(value)
    }
  }
  return options
}

export async function startHmi({root=HMI_ROOT,host='127.0.0.1',port=5173,configFile,env=process.env,buildApp}={}) {
  const build = buildApp || (await import('vite')).build
  // dist is always inside this application. A failed build must not serve stale assets.
  const dist = resolve(root,'dist')
  await build({root,configFile:configFile && resolve(root,configFile),logLevel:'silent',build:{outDir:dist}})
  const allowedHosts = env.__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS?.trim()
  const handler = await createStaticHandler({root:dist,env,allowedHosts:allowedHosts ? [allowedHosts] : []})
  const server = createServer(handler)
  await new Promise((done,fail) => { server.once('error',fail);server.listen(port,host,done) })
  return server
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const server = await startHmi(parseServeArgs(process.argv.slice(2)))
    console.log('HMI built assets ready')
    const stop = () => {
      server.close()
      const timer=setTimeout(()=>server.closeAllConnections(),5000)
      timer.unref()
    }
    process.once('SIGINT',stop);process.once('SIGTERM',stop)
  } catch {
    // Build/HTTP diagnostics can contain environment values; never print them here.
    console.error('HMI asset build or startup failed')
    process.exitCode=1
  }
}
