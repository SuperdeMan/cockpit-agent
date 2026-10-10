import {spawn} from 'node:child_process'
import {writeFileSync,existsSync,mkdirSync} from 'node:fs'
import {resolve,dirname,join} from 'node:path'
import {fileURLToPath} from 'node:url'
import assert from 'node:assert/strict'
import {Cdp,sleep} from './driver.mjs'
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..')
const out=resolve(root,'.artifacts/hmi-map-20261010')
mkdirSync(out,{recursive:true})
const base=process.env.HMI_MAP_URL||'http://127.0.0.1:5197'
// Optional in ordinary fixture runs; required for a credential non-disclosure claim.
const secret=process.env.AMAP_JS_SECURITY_CODE||''
const browserEnv={...process.env}
delete browserEnv.AMAP_JS_KEY
delete browserEnv.AMAP_JS_SECURITY_CODE
const exe=['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(existsSync)
const browser=spawn(exe,['--headless=new','--guest','--no-first-run','--disable-extensions','--remote-debugging-port=9371',`--user-data-dir=${join(out,'browser-profile')}`,'about:blank'],{windowsHide:true,stdio:'ignore',env:browserEnv})
const cdp=new Cdp(),network=[],errors=[]
let secretInBrowser=false
try {
  let target
  for(let i=0;i<80;i++) {try{target=(await(await fetch('http://127.0.0.1:9371/json')).json()).find(p=>p.url==='about:blank');if(target)break}catch{}await sleep(250)}
  assert.ok(target,'isolated browser did not start')
  cdp.ws=new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res,rej)=>{cdp.ws.onopen=res;cdp.ws.onerror=rej})
  cdp.ws.onmessage=event=>{
    const raw=String(event.data)
    if(secret&&raw.includes(secret))secretInBrowser=true
    const message=JSON.parse(raw)
    if(message.method==='Network.responseReceived') {
      const response=message.params.response,url=new URL(response.url)
      if(url.hostname.includes('amap.com')||url.pathname.startsWith('/_AMapService'))network.push({host:url.hostname,path:url.pathname,status:response.status,mime:response.mimeType})
    }
    if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails.text)
    cdp._onMessage(raw)
  }
  for(const domain of ['Page','Runtime','Network'])await cdp.send(domain+'.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false})
  await cdp.send('Page.addScriptToEvaluateOnNewDocument',{source:`
    const NativeSocket=window.WebSocket;
    window.__mapSent=[];
    window.WebSocket=class {static OPEN=1;static CLOSED=3;constructor(url,...args){if(!String(url).includes('localhost:8090/ws'))return new NativeSocket(url,...args);this.readyState=0;window.__mapSocket=this;setTimeout(()=>{this.readyState=1;this.onopen?.({})},10)}send(data){window.__mapSent.push(JSON.parse(data))}close(){this.readyState=3}};
    localStorage.setItem('cockpit.settings.v1',JSON.stringify({ttsEnabled:false,autoplay:false}));
  `})
  await cdp.send('Page.navigate',{url:base+'/?theme=dark'})
  await cdp.waitFor("!!document.querySelector('.au-panel')&&window.__mapSocket?.readyState===1")
  const route={type:'route_plan',estimate:true,origin:'西湖文化广场',destination:'杭州东站',origin_loc:{lat:30.279,lng:120.164},destination_loc:{lat:30.290,lng:120.212},waypoints:[],distance_km:9.8,duration_min:24,
    path:[[30.279,120.164],[30.276,120.164],[30.276,120.176],[30.279,120.176],[30.279,120.196],[30.290,120.196],[30.290,120.212]],_prov:{mode:'mock',vendor:'地图视觉夹具'}}
  const send=async(card,extra={})=>{
    await cdp.eval('window.__mapSocket.onmessage({data:JSON.stringify('+JSON.stringify({type:'final',speech:'地图视觉验证（示例数据）',ui_card:{...card,_prov:route._prov},...extra})+')});true')
    await sleep(250)
  }
  const ready=()=>cdp.waitFor("document.querySelector('.au-map-stage')?.dataset.mapState==='ready'",45000)
  const shot=async name=>{await sleep(700);const {data}=await cdp.send('Page.captureScreenshot',{format:'png'});writeFileSync(join(out,name+'.png'),Buffer.from(data,'base64'))}
  const checks=[]
  const geometry=async(name,count,driving=false)=>{
    await ready();await sleep(200)
    const data=await cdp.eval(`(()=>{
      const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
      return {markers:[...document.querySelectorAll('.au-map-marker')].map(e=>({...rect(e),label:e.textContent,disabled:e.disabled})),panel:rect(document.querySelector('.au-panel')),header:rect(document.querySelector('.au-map-header')),summary:rect(document.querySelector('.au-map-summary')),canvas:document.querySelectorAll('.amap-container canvas').length,width:innerWidth,height:innerHeight};
    })()`)
    writeFileSync(join(out,'geometry-'+name+'.json'),JSON.stringify(data,null,2))
    await shot(name)
    assert.equal(data.markers.length,count,name+' marker count')
    assert.ok(data.canvas>0,name+' real map canvas')
    for(const p of data.markers) {
      assert.ok(p.left>=0&&p.right<=data.width&&p.top>=72&&p.bottom<=data.height,name+' marker inside viewport')
      if(driving)assert.ok(p.disabled,name+' marker disabled while driving')
      else assert.ok(p.left>=data.panel.right,name+' marker avoids conversation')
      assert.ok(p.bottom<=data.header.top||p.top>=data.header.bottom||p.left>=data.header.right||p.right<=data.header.left,name+' marker avoids title')
      assert.ok(p.bottom<=data.summary.top||data.summary.height===0||p.right<=data.summary.left,name+' marker avoids summary')
    }
    checks.push({name,markers:count,geometryPassed:true})
  }
  await send(route);await geometry('route-dark',2)
  await cdp.eval("document.querySelector('.au-map-places button').click();true")
  await cdp.waitFor("document.querySelector('.au-map-marker.selected')?.textContent==='起'")
  await cdp.eval("document.querySelector('[aria-label=打开设置]').click();true")
  await cdp.waitFor("!!document.querySelector('.au-settings-overlay')")
  await cdp.eval("[...document.querySelectorAll('.au-settings-nav button')].find(e=>e.querySelector('span')?.textContent==='显示').click();true")
  await cdp.eval("[...document.querySelectorAll('.au-segmented button')].find(e=>e.textContent==='浅色').click();document.querySelector('[aria-label=关闭设置]').click();true")
  await cdp.waitFor("document.documentElement.dataset.theme==='light'")
  await geometry('route-light',2)
  assert.equal(await cdp.eval("document.querySelector('.au-map-marker.selected')?.textContent"),'起','selection survives theme change')
  await cdp.eval("document.querySelector('[aria-label=打开设置]').click();true")
  await cdp.waitFor("!!document.querySelector('.au-settings-overlay')")
  await cdp.eval("[...document.querySelectorAll('.au-settings-nav button')].find(e=>e.querySelector('span')?.textContent==='显示').click();true")
  await cdp.eval("[...document.querySelectorAll('.au-segmented button')].find(e=>e.textContent==='大字').click();document.querySelector('[aria-label=关闭设置]').click();true")
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height:720,deviceScaleFactor:1,mobile:false})
  await sleep(400);await geometry('route-large-720',2)
  await cdp.send('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false})
  await sleep(300)
  const places={type:'place_list',items:[{name:'缺坐标的地点',id:'none',address:''},{name:'西湖文化广场',id:'a',address:'示例',lat:30.279,lng:120.164},{name:'杭州东站',id:'b',address:'示例',lat:30.290,lng:120.212}]}
  await send(places);await geometry('places-light',2)
  assert.deepEqual(await cdp.eval("[...document.querySelectorAll('.au-map-marker')].map(e=>e.textContent)"),['2','3'])
  await cdp.eval("document.querySelectorAll('.au-map-marker')[1].click();true")
  await cdp.waitFor("document.querySelectorAll('.au-map-places button')[2].getAttribute('aria-pressed')==='true'")
  await send({...route,type:'charging_route',stops:[{name:'补电点',lat:30.279,lng:120.196,at_km:5}],soc_note:'模拟车读数，仅用于视觉验证'});await geometry('charging-light',3)
  await send({type:'trip_itinerary',destination:'杭州',days:1,itinerary:[{day_index:1,stops:[{stop_id:'a',name:'西湖文化广场',type:'attraction',grounded:true,poi:{lat:30.279,lng:120.164}},{stop_id:'b',name:'杭州东站',type:'custom',grounded:true,poi:{lat:30.290,lng:120.212}}],legs:[]}]});await geometry('trip-light',2)
  await send({...route,path:undefined});await geometry('route-without-path',2)
  assert.match(await cdp.eval("document.querySelector('.au-map-header').textContent"),/未提供路线轨迹/)
  await send(route,{driving:true});await cdp.waitFor("document.documentElement.dataset.drive==='on'")
  await geometry('driving-light',2,true)
  await send({...route,cancelled:true},{driving:true})
  await cdp.waitFor("document.querySelector('[data-scene]')?.dataset.scene==='idle'")
  assert.equal(await cdp.eval("document.querySelectorAll('.amap-container').length"),0,'cancel destroys map')
  await cdp.send('Page.navigate',{url:base+'/?theme=dark'})
  await cdp.waitFor("!!document.querySelector('.au-panel')&&window.__mapSocket?.readyState===1")
  await send({type:'poi_list',items:[{id:'none',name:'没有坐标',address:'未知'}]})
  await cdp.waitFor("document.querySelector('.au-map-stage')?.dataset.mapState==='schematic'")
  assert.equal(await cdp.eval("!!window.AMap"),false,'no SDK request without coordinates')
  await shot('no-coordinates')
  await cdp.send('Network.setBlockedURLs',{urls:['*webapi.amap.com/maps*']})
  await send(route)
  await cdp.waitFor("document.querySelector('.au-map-stage')?.dataset.mapState==='unavailable'",25000)
  await shot('sdk-unavailable')
  await cdp.send('Network.setBlockedURLs',{urls:[]})
  await cdp.eval("document.querySelector('.au-map-tools button').click();true")
  await geometry('retry-dark',2)
  assert.equal(await cdp.eval('window.__mapSent.length'),0,'map interactions do not send business events')
  assert.equal(secretInBrowser,false,'security code reached browser')
  const evidence={checks,missingCoordinates:true,cancelClearsMap:true,retry:true,numberingPreserved:true,businessFrames:0,network,errors,secretChecked:!!secret,secretInBrowser}
  writeFileSync(join(out,'browser-checks.json'),JSON.stringify(evidence,null,2))
  console.log(JSON.stringify({checks:checks.length,missingCoordinates:true,cancelClearsMap:true,retry:true,numberingPreserved:true,businessFrames:0,errors,secretChecked:!!secret,secretInBrowser}))
} finally {
  if(cdp.ws?.readyState===WebSocket.OPEN){try{await cdp.send('Browser.close')}catch{}cdp.ws.close()}
  else browser.kill()
}
