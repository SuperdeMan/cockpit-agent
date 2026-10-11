// Test-only A/B servers: Vite hosts both the dev app and the production HTTP handler.
// The invalid backend addresses and mock browser WebSocket keep this benchmark offline.
import {createServer,build} from '../../hmi/node_modules/vite/dist/node/index.js'
import {resolve,join} from 'node:path'
import {mkdirSync,writeFileSync} from 'node:fs'
import {createStaticHandler} from '../../hmi/server/static.mjs'
const root=resolve(import.meta.dirname,'../..'),app=join(root,'hmi')
const output=join(root,'.artifacts/hmi-startup-servers')
mkdirSync(output,{recursive:true})
Object.assign(process.env,{VITE_EDGE_GATEWAY_URL:'https://edge.hmi-test.invalid:8443',VITE_AUDIO_API_URL:'https://audio.hmi-test.invalid:8444',VITE_WS_TOKEN:'fixture-browser-token-not-a-real-credential'})
const started=Date.now()
await build({root:app,configFile:join(app,'vite.config.ts'),logLevel:'warn'})
const buildMs=Date.now()-started
// build() sets NODE_ENV=production; reset it for the separate development baseline.
process.env.NODE_ENV='development'
const handler=await createStaticHandler({root:join(app,'dist'),env:{}})
const production=await createServer({root:app,configFile:false,publicDir:false,appType:'custom',cacheDir:join(output,'vite-wrapper-cache'),optimizeDeps:{noDiscovery:true,include:[]},server:{host:'127.0.0.1',port:5195,strictPort:true},plugins:[{name:'test-built-handler',configureServer(s){s.middlewares.use(handler)}}]})
const development=await createServer({root:app,configFile:join(app,'vite.config.ts'),server:{host:'127.0.0.1',port:5194,strictPort:true}})
await production.listen();await development.listen()
writeFileSync(join(output,'servers.json'),JSON.stringify({buildMs,dev:'http://127.0.0.1:5194',built:'http://127.0.0.1:5195'},null,2))
console.log(JSON.stringify({buildMs,dev:5194,built:5195,viteOnly:true}))
const close=async()=>{await development.close();await production.close();process.exit()}
process.once('SIGINT',close);process.once('SIGTERM',close)
