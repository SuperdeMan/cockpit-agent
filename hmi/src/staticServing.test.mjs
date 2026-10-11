import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer, request } from 'node:http'
import { spawnSync } from 'node:child_process'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import { gunzipSync } from 'node:zlib'
import { createStaticHandler, acceptsGzip, ISOLATION_HEADERS } from '../server/static.mjs'
import { parseServeArgs, startHmi } from '../server/serve.mjs'

const runtime={AMAP_JS_KEY:'fixture-application-key',AMAP_JS_SECURITY_CODE:'fixture-server-security-code'}
const js='export const answer = 42;\n'.repeat(400)

async function fixture(t) {
  const parent=await mkdtemp(join(tmpdir(),'hmi-static-'))
  const root=join(parent,'dist')
  await mkdir(join(root,'assets'),{recursive:true})
  await writeFile(join(root,'index.html'),'<html><script type="module" src="/assets/app-abcdefgh.js"></script></html>')
  await writeFile(join(root,'assets','app-abcdefgh.js'),js)
  await writeFile(join(root,'model.wasm'),Buffer.from([0,97,115,109,1,0,0,0]))
  await writeFile(join(root,'.env'),'NEVER_SERVE_THIS')
  await writeFile(join(parent,'private.txt'),'OUTSIDE_BUILD')
  const server=createServer(await createStaticHandler({root,env:runtime,allowedHosts:['hmi.example.test']}))
  await new Promise(done=>server.listen(0,'127.0.0.1',done))
  t.after(async()=>{
    server.closeAllConnections();await new Promise(done=>server.close(done))
    assert.ok(resolve(parent).startsWith(resolve(tmpdir())+sep+'hmi-static-'))
    await rm(parent,{recursive:true,force:true})
  })
  const send=(path,options={})=>new Promise((done,fail)=>{
    const req=request({hostname:'127.0.0.1',port:server.address().port,path,...options},res=>{
      const chunks=[];res.on('data',c=>chunks.push(c));res.on('end',()=>done({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}))
    });req.on('error',fail);req.end()
  })
  return {send,parent,root}
}

test('built entry and WASM preserve content types and voice isolation headers',async t=>{
  const {send}=await fixture(t)
  const page=await send('/?theme=light')
  assert.equal(page.status,200);assert.match(page.body.toString(),/assets\/app-abcdefgh\.js/)
  assert.equal(page.headers['cache-control'],'no-store')
  for(const [key,value] of Object.entries(ISOLATION_HEADERS))assert.equal(page.headers[key.toLowerCase()],value)
  const wasm=await send('/model.wasm')
  assert.equal(wasm.headers['content-type'],'application/wasm')
  assert.deepEqual(wasm.body,Buffer.from([0,97,115,109,1,0,0,0]))
  assert.match(wasm.headers['cache-control'],/must-revalidate/)
})

test('hash assets are compressed and privately cached; HEAD and ETag avoid payload',async t=>{
  const {send}=await fixture(t)
  const zipped=await send('/assets/app-abcdefgh.js',{headers:{'Accept-Encoding':'gzip'}})
  assert.equal(zipped.status,200);assert.equal(zipped.headers['content-encoding'],'gzip')
  assert.equal(gunzipSync(zipped.body).toString(),js)
  assert.ok(zipped.body.length<Buffer.byteLength(js)/4)
  assert.match(zipped.headers['cache-control'],/private.*immutable/)
  assert.equal(zipped.headers.vary,'Accept-Encoding')
  const cached=await send('/assets/app-abcdefgh.js',{headers:{'If-None-Match':zipped.headers.etag,'Accept-Encoding':'gzip'}})
  assert.equal(cached.status,304);assert.equal(cached.body.length,0)
  const head=await send('/assets/app-abcdefgh.js',{method:'HEAD',headers:{'Accept-Encoding':'identity'}})
  assert.equal(head.status,200);assert.equal(Number(head.headers['content-length']),Buffer.byteLength(js));assert.equal(head.body.length,0)
  const plain=await send('/assets/app-abcdefgh.js',{headers:{'Accept-Encoding':'gzip;q=0, br'}})
  assert.equal(plain.headers['content-encoding'],undefined);assert.equal(plain.body.toString(),js)
})

test('host, method and build allowlist reject source, dotfiles and traversal',async t=>{
  const {send}=await fixture(t)
  for(const host of ['evil.test','evil@localhost','localhost/elsewhere','localhost#fragment'])
    assert.equal((await send('/',{headers:{Host:host}})).status,403,host)
  assert.equal((await send('/',{headers:{Host:'hmi.example.test'}})).status,200)
  assert.equal((await send('/',{method:'POST'})).status,405)
  for(const path of ['/src/main.tsx','/@vite/client','/node_modules/vite/package.json','/.env','/%2eenv','/../private.txt','/%2e%2e%2fprivate.txt','/assets/app-abcdefgh.js%00']) {
    const r=await send(path);assert.equal(r.status,404,path);assert.ok(!r.body.includes('NEVER_SERVE_THIS'));assert.ok(!r.body.includes('OUTSIDE_BUILD'))
  }
  assert.equal((await send('/%XX')).status,400)
})

test('the built-assets server retains the map config/proxy boundary',async t=>{
  const {send}=await fixture(t)
  const config=await send('/api/maps/config')
  assert.equal(config.status,200)
  assert.deepEqual(JSON.parse(config.body),{available:true,key:runtime.AMAP_JS_KEY})
  assert.equal(config.headers['cache-control'],'no-store')
  assert.ok(!config.body.includes(runtime.AMAP_JS_SECURITY_CODE))
  assert.equal((await send('/api/maps/config',{headers:{'Sec-Fetch-Site':'cross-site'}})).status,403)
  assert.equal((await send('/_AMapService/v3/place/text?key='+runtime.AMAP_JS_KEY)).status,403)
  assert.equal((await send('/_AMapService/v3/log/init?key='+runtime.AMAP_JS_KEY,{method:'POST'})).status,405)
})

test('gzip negotiation respects explicit rejection and wildcard preferences',()=>{
  for(const value of ['gzip','br, gzip;q=0.5','*;q=0.2'])assert.equal(acceptsGzip(value),true,value)
  for(const value of ['', 'br','gzip;q=0, *;q=1','gzip;q=invalid'])assert.equal(acceptsGzip(value),false,value)
})

test('production launcher validates options and never listens after a failed build',async()=>{
  assert.deepEqual(parseServeArgs(['--host','0.0.0.0','--port','5173','--config','vite.cloud.config.mjs']),{host:'0.0.0.0',port:5173,configFile:'vite.cloud.config.mjs'})
  for(const args of [['--unknown','x'],['--port','0'],['--port','65536'],['--config']])assert.throws(()=>parseServeArgs(args))
  let built=false
  await assert.rejects(startHmi({root:tmpdir(),port:0,buildApp:async()=>{built=true;throw new Error('fixture build failed')}}),/fixture build failed/)
  assert.equal(built,true)
})

test('startup builds with runtime client configuration while keeping the map secret out of assets',async t=>{
  const root=await mkdtemp(join(tmpdir(),'hmi-static-'))
  t.after(async()=>{
    assert.ok(resolve(root).startsWith(resolve(tmpdir())+sep+'hmi-static-'))
    await rm(root,{recursive:true,force:true})
  })
  await writeFile(join(root,'index.html'),'<script type="module" src="/main.js"></script>')
  await writeFile(join(root,'main.js'),'window.config=[import.meta.env.VITE_EDGE_GATEWAY_URL,import.meta.env.VITE_AUDIO_API_URL,import.meta.env.VITE_WS_TOKEN]')
  const moduleUrl=new URL('../server/serve.mjs',import.meta.url).href
  const script=`import assert from 'node:assert/strict';
    const {startHmi}=await import(${JSON.stringify(moduleUrl)});
    const server=await startHmi({root:${JSON.stringify(root)},host:'127.0.0.1',port:0,configFile:false});
    try {
      const base='http://127.0.0.1:'+server.address().port;
      const page=await fetch(base).then(r=>r.text());
      const asset=page.match(/src="([^"]+)"/)[1];
      const body=await fetch(base+asset).then(r=>r.text());
      for(const name of ['VITE_EDGE_GATEWAY_URL','VITE_AUDIO_API_URL','VITE_WS_TOKEN'])assert.ok(body.includes(process.env[name]));
      assert.ok(!body.includes(process.env.AMAP_JS_SECURITY_CODE));
      assert.equal((await fetch(base+'/src/main.tsx')).status,404);
      console.log('runtime configuration preserved');
    } finally {server.closeAllConnections();await new Promise(r=>server.close(r))}`
  const run=spawnSync(process.execPath,['--input-type=module','--eval',script],{encoding:'utf8',timeout:30000,windowsHide:true,
    env:{...process.env,NODE_ENV:'production',VITE_EDGE_GATEWAY_URL:'https://edge.fixture.invalid:8443',VITE_AUDIO_API_URL:'https://audio.fixture.invalid:8444',VITE_WS_TOKEN:'fixture-client-credential',...runtime}})
  assert.equal(run.status,0,run.stderr)
  assert.match(run.stdout,/runtime configuration preserved/)
})
