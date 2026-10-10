import test from 'node:test'
import assert from 'node:assert/strict'
import {EventEmitter} from 'node:events'
import {Readable} from 'node:stream'
import {createMapMiddleware,mapCredentials,mapUpstream} from '../server/amap.mjs'

const env={AMAP_JS_KEY:'fixture-application-key',AMAP_JS_SECURITY_CODE:'fixture-security-code'}
const credentials=mapCredentials(env)
const valid='/_AMapService/v3/log/init?key='+env.AMAP_JS_KEY
function invoke(url,{method='GET',headers={},runtime=env,request}={}) {
  return new Promise(resolve=>{
    const res=new EventEmitter()
    res.writeHead=(status,headers)=>{res.status=status;res.headers=headers}
    res.end=body=>resolve({status:res.status,body:String(body),headers:res.headers})
    createMapMiddleware({env:runtime,request})({url,method,headers},res,()=>resolve({next:true}))
  })
}
function upstream(body,status=200) {
  return (_url,_options,callback)=>{
    const req=new EventEmitter()
    req.setTimeout=()=>{}
    req.destroy=()=>{}
    req.end=()=>{
      const response=Readable.from([Buffer.from(body)])
      response.statusCode=status
      response.headers={'content-type':'application/json'}
      callback(response)
    }
    return req
  }
}
test('runtime config never returns the security code and is not cached',async()=>{
  const result=await invoke('/api/maps/config')
  assert.deepEqual(JSON.parse(result.body),{available:true,key:env.AMAP_JS_KEY})
  assert.ok(!result.body.includes(env.AMAP_JS_SECURITY_CODE))
  assert.equal(result.headers['Cache-Control'],'no-store')
  assert.deepEqual(JSON.parse((await invoke('/api/maps/config',{runtime:{}})).body),{available:false})
})
test('proxy binds key and destination; no caller URL, search or route planner',()=>{
  for(const url of ['/unrelated', '/_AMapService/v3/place/text?key='+env.AMAP_JS_KEY,'/_AMapService/v3/direction/driving?key='+env.AMAP_JS_KEY,
    '/_AMapService/https://evil.test/',valid+'&key=other','/_AMapService/v3/log/init?key=wrong']) assert.equal(mapUpstream(url,credentials),null)
  const result=mapUpstream(valid+'&jscode=attacker',credentials)
  assert.equal(result.origin,'https://restapi.amap.com')
  assert.deepEqual(result.searchParams.getAll('jscode'),[env.AMAP_JS_SECURITY_CODE])
  assert.equal(mapUpstream('/_AMapService/v4/map/styles?key='+env.AMAP_JS_KEY,credentials).origin,'https://webapi.amap.com')
})
test('reject cross-site, mutation and unavailable requests before network calls',async()=>{
  const never=()=>{throw new Error('must not dispatch')}
  assert.equal((await invoke(valid,{method:'POST',request:never})).status,405)
  assert.equal((await invoke(valid,{headers:{'sec-fetch-site':'cross-site'},request:never})).status,403)
  assert.equal((await invoke(valid,{headers:{'sec-fetch-site':'same-site'},request:never})).status,403)
  assert.equal((await invoke(valid,{headers:{origin:'https://elsewhere.invalid',host:'hmi.invalid'},request:never})).status,403)
  assert.equal((await invoke(valid,{runtime:{},request:never})).status,503)
  assert.equal((await invoke('/_AMapService/v3/place/text?key='+env.AMAP_JS_KEY,{request:never})).status,403)
})
test('proxy preserves successful map responses, but suppresses secret-bearing upstream errors',async()=>{
  assert.equal((await invoke(valid,{request:upstream('{"ok":true}')})).body,'{"ok":true}')
  for(const options of [{request:upstream(env.AMAP_JS_SECURITY_CODE)}, {request:upstream('diagnostic '+env.AMAP_JS_SECURITY_CODE,403)},
    {request:()=>{throw new Error(env.AMAP_JS_SECURITY_CODE)}}]) {
    const result=await invoke(valid,options)
    assert.equal(result.status,502)
    assert.ok(!result.body.includes(env.AMAP_JS_SECURITY_CODE))
    assert.ok(!result.body.includes(env.AMAP_JS_KEY))
  }
})
test('oversized upstream bodies cannot be forwarded',async()=>{
  assert.equal((await invoke(valid,{request:upstream('x'.repeat(2*1024*1024+1))})).status,502)
})
