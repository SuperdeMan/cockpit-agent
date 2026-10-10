import {useEffect,useMemo,useRef,useState} from 'react'
import {useDriving} from '../DrivingContext'
import {useSettings} from '../settings'
import {mapViewPadding,projectMap,type MapModel} from '../mapPresentation.mjs'
import {createMapView,loadMapSdk,type MapView} from '../mapSdk.mjs'
import {present} from '../cardPresentation.mjs'
import {ProvBadge} from './Cards'
import {Icon} from './Icon'
import type {Provenance,UiCard} from '../types'

function Schematic({model}:{model:MapModel}) {
  const nodes=model.entries.map((entry,index)=>({...entry,x:100+index/Math.max(1,model.entries.length-1)*680,y:390-index/Math.max(1,model.entries.length-1)*250}))
  let line=nodes.map(p=>p.x+','+p.y).join(' ')
  if(model.path.length>1) {
    const lngs=model.path.map(p=>p[0]),lats=model.path.map(p=>p[1])
    const x0=Math.min(...lngs),y0=Math.min(...lats)
    line=model.path.map(([lng,lat])=>(100+(lng-x0)/Math.max(.00001,Math.max(...lngs)-x0)*680)+','+(430-(lat-y0)/Math.max(.00001,Math.max(...lats)-y0)*300)).join(' ')
  }
  return <div className="au-map-fallback"><svg viewBox="0 0 880 520" role="img" aria-label="地点顺序示意，不代表实际地理位置">
    <defs><pattern id="stage-grid" width="80" height="80" patternUnits="userSpaceOnUse"><path d="M80 0H0V80" fill="none" stroke="var(--au-map-road-minor)" /></pattern></defs>
    <rect width="880" height="520" fill="url(#stage-grid)" />
    {model.route&&nodes.length>1&&<polyline points={line} fill="none" stroke="var(--au-map-route)" strokeWidth="4" strokeDasharray={model.path.length?'0':'12 10'} />}
    {nodes.map((p,index)=><g key={index}><circle cx={p.x} cy={p.y} r="24" fill="var(--au-primary)" /><text x={p.x} y={p.y+8} textAnchor="middle" fontSize="24" fill="var(--au-primary-ink)">{p.label}</text></g>)}
  </svg><p className="au-stage-caption">{model.path.length?'路线形状来自返回的路径点；标注仅作示意。':'按结果顺序示意，不代表实际地理位置。'}</p></div>
}

export function MapStage({card}:{card:UiCard}) {
  const model=useMemo(()=>projectMap(card),[card])
  const {driving}=useDriving()
  const {settings}=useSettings()
  const canvas=useRef<HTMLDivElement>(null)
  const header=useRef<HTMLElement>(null)
  const summary=useRef<HTMLDivElement>(null)
  const view=useRef<MapView|null>(null)
  const latest=useRef({model,driving,theme:settings.theme})
  latest.current={model,driving,theme:settings.theme}
  const [status,setStatus]=useState<'loading'|'ready'|'unavailable'>('loading')
  const [reason,setReason]=useState('')
  const [attempt,setAttempt]=useState(0)
  const [selected,setSelected]=useState(-1)
  const details=card as unknown as {distance_km?:number;duration_min?:number;estimate?:boolean;soc_note?:string;_prov?:Provenance}
  useEffect(()=>{
    if(!model.hasCoordinates||!canvas.current) return
    let active=true
    const element=canvas.current
    setStatus('loading');setReason('');setSelected(-1)
    const padding=()=>{
      const bounds=element.getBoundingClientRect()
      const panel=document.querySelector('.au-panel')?.getBoundingClientRect()
      return mapViewPadding({width:bounds.width,height:bounds.height,panelRight:panel?.right,
        headerBottom:header.current?.getBoundingClientRect().bottom,
        summaryHeight:summary.current?.offsetHeight,
        answerHeight:latest.current.driving&&panel?bounds.bottom-panel.top:0,
        driving:latest.current.driving})
    }
    const fail=(message:string)=>{
      if(!active) return
      view.current?.destroy();view.current=null
      setReason(message);setStatus('unavailable')
    }
    loadMapSdk().then(sdk=>{
      if(!active) return
      view.current=createMapView(sdk,element,{...latest.current,padding,
        onReady:()=>{if(active)setStatus('ready')},onError:fail,onSelect:setSelected})
    }).catch(error=>fail(error instanceof Error&&error.message==='地图尚未配置'?'地图尚未配置':'地图服务暂时不可用'))
    const resize=new ResizeObserver(()=>view.current?.fit())
    resize.observe(element)
    if(summary.current)resize.observe(summary.current)
    if(header.current)resize.observe(header.current)
    const panel=document.querySelector('.au-panel')
    if(panel)resize.observe(panel)
    return()=>{active=false;resize.disconnect();view.current?.destroy();view.current=null}
  },[model.hasCoordinates,attempt])
  useEffect(()=>{setSelected(-1);view.current?.setModel(model)},[model])
  useEffect(()=>{const frame=requestAnimationFrame(()=>view.current?.setTheme(settings.theme));return()=>cancelAnimationFrame(frame)},[settings.theme])
  useEffect(()=>{const frame=requestAnimationFrame(()=>view.current?.setDriving(driving));return()=>cancelAnimationFrame(frame)},[driving])
  useEffect(()=>{view.current?.select(selected)},[selected])
  const displayStatus=model.cancelled?'cancelled':!model.hasCoordinates?'schematic':status
  const fallback=displayStatus==='schematic'||displayStatus==='unavailable'
  return <section className="au-map-stage" data-map-state={displayStatus}>
    <div className="au-map-canvas" ref={canvas} aria-label="地图底图" />
    {fallback&&<Schematic model={model} />}
    <header ref={header} className="au-map-header">
      <div className="au-map-title"><Icon name="route-map" size={28} color="var(--au-primary)" /><h2>{model.title}</h2></div>
      <div className="au-stage-map-meta">{details.estimate&&<span>估算</span>}{present(details.distance_km)&&<span>{details.distance_km} km</span>}{present(details.duration_min)&&<span>约 {details.duration_min} 分钟</span>}<ProvBadge prov={details._prov} /></div>
      {displayStatus==='loading'&&<span className="au-map-loading" role="status">正在加载地图…</span>}
      {fallback&&<span className="au-stage-map-notice" role="status"><Icon name="warning" size={24} /><span>示意 · {displayStatus==='schematic'?'暂无可用坐标':reason}</span></span>}
      {displayStatus==='ready'&&model.pathMissing&&<span className="au-stage-caption">未提供路线轨迹，仅显示地点</span>}
      {displayStatus==='ready'&&model.missingCoordinates>0&&<span className="au-stage-caption">{model.missingCoordinates} 个地点缺少坐标，未在地图标注</span>}
    </header>
    {!driving&&<div className="au-map-tools">
      {displayStatus==='ready'&&<button onClick={()=>view.current?.fit()}>全览</button>}
      {displayStatus==='unavailable'&&<button onClick={()=>setAttempt(n=>n+1)}>重试地图</button>}
    </div>}
    <div ref={summary} className="au-map-summary">
      <div className="au-map-summary-title">{model.cancelled?'这条导航已结束':model.route?(details.estimate?'路线估算':'路线信息'):'地点一览'}</div>
      {!driving&&<div className="au-map-places">{model.entries.map((entry,index)=><button key={index} className={selected===index?'selected':''}
        aria-pressed={selected===index} onClick={()=>setSelected(index)} disabled={!entry.position||displayStatus!=='ready'}>
        <b className="au-num">{entry.label}</b><span>{entry.name}{entry.detail&&<small>{entry.detail}</small>}{!entry.position&&<small>暂无坐标</small>}</span>
      </button>)}</div>}
      {card.type==='charging_route'&&card.stops?.length===0&&<p className="au-stage-caption">全程无需补电</p>}
      {details.soc_note&&<p className="au-stage-caption">{details.soc_note}</p>}
      {!model.cancelled&&<p className="au-stage-caption">{model.route?'路线预览':'点选编号查看对应地点'}</p>}
    </div>
  </section>
}
