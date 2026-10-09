// Visual v2 I5: read-only contextual stages; all controls still send ordinary utterances.
import { useEffect, useState } from 'react'
import { useDriving } from '../DrivingContext'
import { useStageView } from '../StageView'
import { selectStage } from '../stagePresentation.mjs'
import { stageMetrics } from '../vehicleStage.mjs'
import { groupByDay, resolveView } from '../reminderStage.mjs'
import { present, cardTitle } from '../cardPresentation.mjs'
import { CardRenderer, ProvBadge } from './Cards'
import { Icon } from './Icon'
import { AQISection } from './aurora'
import type { Msg, UiCard, WeatherCard, ForecastCard, ReminderItem, Provenance } from '../types'

export function ContextualStage({messages,vehicle={},vehicleLabel,onAction}: {
  messages: Msg[]; vehicle?:Record<string,unknown>; vehicleLabel?:string; onAction?:(text:string)=>void
}) {
  const {driving}=useDriving()
  const view=useStageView()
  const scene=selectStage(messages,driving,view?.selected)
  return <div className={'au-stage-content scene-'+scene.kind} data-scene={scene.kind}>
    {view?.selected && !driving && <button className="au-stage-back" onClick={view.close}>回到当前内容</button>}
    {scene.kind==='weather' && scene.card ? <WeatherStage card={scene.card as WeatherCard|ForecastCard} />
      : scene.kind==='map' && scene.card ? <MapStage card={scene.card} />
      : scene.kind==='agenda' && scene.card ? <AgendaStage card={scene.card} />
      : (scene.kind==='reading'||scene.kind==='payment') && scene.card && !driving ? <div className={'au-stage-document '+(scene.kind==='payment'?'payment':'')}>
          <div className="au-stage-document-label">{cardTitle(scene.card)} · 仅在非行车界面展开</div>
          <CardRenderer card={scene.card} stage onAction={onAction} />
        </div>
      : scene.kind==='vehicle' ? <VehicleStage vehicle={vehicle} label={vehicleLabel} />
      : scene.kind==='media' ? <MediaStage message={scene.message} onAction={onAction} />
      : <IdleStage vehicle={vehicle} label={vehicleLabel} />}
  </div>
}
function Source({card}:{card:UiCard}) { return <ProvBadge prov={(card as {_prov?:Provenance})._prov} /> }

function IdleStage({vehicle,label}:{vehicle:Record<string,unknown>;label?:string}) {
  const [battery,range,gear]=stageMetrics(vehicle)
  const pct=Number(battery.value)
  const available=Number.isFinite(pct)
  return <section className="au-idle-stage">
    <header><h2>此刻的车</h2><span className="au-vehicle-source">{label || '车况读不到'}</span></header>
    <div className="au-idle-body">
      <div className={'au-battery-dial'+(!available?' unavailable':'')} style={{background:available?'conic-gradient(var(--au-primary) '+Math.max(0,Math.min(100,pct))+'%, var(--au-surface-2) 0)':'var(--au-surface-2)'}}>
        <div><strong className={available?'au-num':''}>{battery.value}</strong>{battery.unit && <span>{battery.unit}</span>}<small>电量</small></div>
      </div>
      <div className="au-idle-metrics">{[range,gear].map(m=><div key={m.label}>
        <label>{m.label}</label><div><strong className={m.value==='读不到'?'unavailable':'au-num'}>{m.value}</strong><span>{m.unit}</span></div>
      </div>)}</div>
    </div>
  </section>
}
function WeatherStage({card}:{card:WeatherCard|ForecastCard}) {
  const weather=card.type==='weather'?card:null
  const days=card.type==='forecast'?card.days:card.forecast||[]
  const focus=weather?.focus
  const value=focus ? [focus.temp_low,focus.temp_high].filter(present).join('～') : weather?.temp
  const description=focus?.text_day || weather?.text || '天气预报'
  return <section className="au-weather-stage">
    <h2>{card.city}{focus?.label?' · '+focus.label:''}</h2>
    <Source card={card} />
    <div className="au-weather-main"><strong className={present(value)?'au-num':'unavailable'}>{present(value)?value:card.type==='forecast'?'未来 '+days.length+' 天':'读不到'}</strong>{present(value)&&<span>°C</span>}</div>
    <h3>{description}</h3>
    {!!days.length && <div className="au-stage-forecast">{days.slice(0,3).map((d,i)=><div key={i}>
      <label>{d.date}</label><div><Icon name={String(d.text_day).includes('雨')?'weather-rain':String(d.text_day).includes('晴')?'weather-sunny':'weather-cloudy'} size={28} />{d.text_day}</div>
      <strong className="au-num">{d.temp_low}° / {d.temp_high}°</strong>
    </div>)}</div>}
    {weather?.air_quality && <AQISection aqi={weather.air_quality.aqi} category={weather.air_quality.category} />}
  </section>
}

type Node = {name:string;detail?:string;x:number;y:number}
function mapNodes(card:UiCard):Node[] {
  const c=card as any
  let values:Array<{name:string;detail?:string}>=[]
  if(c.type==='poi_list'||c.type==='place_list') values=(c.items||[]).slice(0,6).map((p:any)=>({name:p.name,detail:present(p.distance_km)?p.distance_km+' km':''}))
  else if(c.type==='poi_detail'||c.type==='place_detail') values=[{name:c.name,detail:c.address}]
  else if(c.type==='trip_itinerary') values=(c.itinerary||[]).flatMap((day:any)=>(day.stops||[]).map((s:any)=>({name:s.name,detail:'第 '+day.day_index+' 天'}))).slice(0,8)
  else values=[{name:c.origin || '出发地'},...(c.waypoints||c.stops||[]).map((s:any)=>({name:s.name,detail:present(s.at_km)?'约 '+s.at_km+' km 处补电':s.address})),{name:c.destination}].filter(x=>x.name)
  return values.map((item,index)=>({...item,x:120+(values.length>1?index/(values.length-1):.5)*680,y:values.length>1?450-index/(values.length-1)*260+(index%2?24:0):300}))
}
function MapStage({card}:{card:UiCard}) {
  const c=card as any
  const nodes=mapNodes(card)
  const path:Array<[number,number]>=Array.isArray(c.path)?c.path.filter((p:any)=>Array.isArray(p)&&p.length===2&&p.every((n:any)=>typeof n==='number'&&Number.isFinite(n))):[]
  let line=nodes.map(p=>p.x+','+p.y).join(' ')
  if(path.length>1) {
    const lats=path.map(p=>p[0]), lngs=path.map(p=>p[1])
    const loLat=Math.min(...lats),hiLat=Math.max(...lats),loLng=Math.min(...lngs),hiLng=Math.max(...lngs)
    const projected=path.map(p=>({x:100+(p[1]-loLng)/Math.max(.00001,hiLng-loLng)*700,y:490-(p[0]-loLat)/Math.max(.00001,hiLat-loLat)*330}))
    line=projected.map(p=>p.x+','+p.y).join(' ')
    nodes.forEach((node,index)=>Object.assign(node,projected[Math.round(index/Math.max(1,nodes.length-1)*(projected.length-1))]))
  }
  return <section className="au-map-stage">
    <header><span className="au-stage-map-notice"><Icon name="warning" size={24} color="var(--au-warn)" />示意 · 地图底图暂不可用</span>
      <h2>{c.cancelled?'导航已取消':c.title || c.destination || c.name || cardTitle(card)}</h2>
      <div className="au-stage-map-meta">{c.estimate&&<span>估算 · </span>}{present(c.distance_km)&&<span>{c.distance_km} km </span>}{present(c.duration_min)&&<span> · {c.duration_min} 分钟</span>}<Source card={card} /></div>
    </header>
    {!c.cancelled && <svg className="au-stage-map-svg" viewBox="0 0 920 600" role="img" aria-label="结果位置与路线示意">
      <defs><pattern id="stage-grid" width="80" height="80" patternUnits="userSpaceOnUse"><path d="M80 0H0V80" fill="none" stroke="var(--au-map-road-minor)" strokeWidth="1" /></pattern></defs>
      <rect width="920" height="600" fill="url(#stage-grid)" />
      {nodes.length>1&&<polyline points={line} fill="none" stroke="var(--au-map-route)" strokeWidth="4" strokeDasharray={path.length>1?undefined:'12 10'} strokeLinecap="round" strokeLinejoin="round" />}
      {nodes.map((p,i)=><g key={i}>
        <circle cx={p.x} cy={p.y} r={22} fill="var(--au-primary)" />
        <text x={p.x} y={p.y+8} textAnchor="middle" fill="var(--au-primary-ink)" fontSize="24" fontFamily="var(--au-font-mono)">{i+1}</text>
        <text x={p.x} y={p.y-36} textAnchor="middle" fill="var(--au-text)" fontSize="24"><title>{p.name}</title>{p.name.length>11?p.name.slice(0,10)+'…':p.name}</text>
      </g>)}
    </svg>}
    <div className="au-map-details">
      {c.cancelled?<p>这条导航已结束。</p>:nodes.map((p,i)=><div key={i}><b className="au-num">{i+1}</b><span>{p.name}</span>{p.detail&&<small>{p.detail}</small>}</div>)}
      {c.type==='charging_route'&&c.stops?.length===0&&<p>全程无需补电</p>}
      {c.soc_note&&<p>{c.soc_note}</p>}
    </div>
    <div className="au-stage-caption">{path.length>1?'路线形状来自返回的路径点；标注仅作示意。':'按结果顺序示意，不代表实际地理位置。'}路况以导航播报为准。</div>
  </section>
}
function AgendaStage({card}:{card:UiCard}) {
  const c=card as any
  const [now,setNow]=useState(Date.now)
  useEffect(()=>{const t=window.setInterval(()=>setNow(Date.now()),30000);return()=>window.clearInterval(t)},[])
  const items:ReminderItem[]=c.type==='reminder_card'?[c.item].filter(Boolean):c.items||[]
  const view=resolveView(c)
  const {groups,more}=groupByDay(items,now,12)
  const undated=items.filter(i=>!i.fire_at_ms)
  const shown=[...groups,...(undated.length?[{label:'时间待确认',items:undated}]:[])]
  return <section className="au-agenda-stage"><h2>{c.date_label || (view==='day'?'当天日程':'日程')}</h2><Source card={card} />
    {!items.length&&<p>没有查到日程。</p>}
    {shown.map((g,i)=><div className="au-agenda-group" key={i}><h3>{g.label}</h3>
      {g.items.map(item=><div className="au-agenda-item" key={item.id} data-status={item.status}>
        <time className="au-num">{item.time_display || (item.fire_at_ms?new Date(item.fire_at_ms).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'未提供时间')}</time>
        <span><strong>{item.title}</strong>{item.recur_label&&<small>{item.recur_label}</small>}</span>
        <label>{item.status==='done'?'已完成':item.status==='fired'?'提醒到点':item.status==='cancelled'?'已取消':'待提醒'}</label>
      </div>)}</div>)}
    {!!more&&<p>还有 {more} 条日程</p>}
    {!!c.todos?.length&&<div className="au-agenda-group"><h3>待办</h3>{c.todos.map((item:ReminderItem)=><div className="au-agenda-item" key={item.id}><Icon name="check-circle" size={24} /><span>{item.title}</span><label>{item.status==='done'?'已完成':'待办'}</label></div>)}</div>}
  </section>
}
function VehicleStage({vehicle,label}:{vehicle:Record<string,unknown>;label?:string}) {
  const fields=[['车窗','window'],['座椅','seat'],['空调','aircon'],['车门锁','door_lock'],['后备箱','trunk']]
  const text=(value:unknown):string=>{
    if(value==null) return '读不到'
    if(typeof value==='object') {
      const names:Record<string,string>={temp:'温度',temperature:'温度',on:'开启',heating:'加热',ventilation:'通风',speed:'风量',mode:'模式',locked:'上锁'}
      const parts=Object.entries(value as Record<string,unknown>).filter(([k,v])=>names[k]&&v!=null).map(([k,v])=>names[k]+' '+text(v))
      return parts.join(' · ') || '状态详情暂不可读'
    }
    if(typeof value==='boolean') return value?'是':'否'
    const words:Record<string,string>={OPEN:'打开',CLOSED:'关闭',ON:'开启',OFF:'关闭',LOCKED:'已上锁',UNLOCKED:'未上锁'}
    return words[String(value).toUpperCase()] || String(value)
  }
  return <section className="au-vehicle-stage"><header><h2>此刻的车</h2><span className="au-vehicle-source">{label || '车况读不到'}</span></header>
    <div className="au-vehicle-layout">
      <svg viewBox="0 0 240 440" role="img" aria-label="通用车辆俯视示意">
        <path d="M60 36Q120 5 180 36Q208 66 208 136V332Q208 414 120 420Q32 414 32 332V136Q32 66 60 36Z" fill="var(--au-surface-1)" stroke="var(--au-line-2)" strokeWidth="3"/>
        <path d="M54 134Q120 100 186 134L172 194Q120 176 68 194Z M66 316Q120 335 174 316L184 364Q120 390 56 364Z" fill="var(--au-primary-soft)" stroke="var(--au-line-2)" strokeWidth="2"/>
        <path d="M52 152L48 318M188 152L192 318M74 214Q89 196 104 214V280Q89 292 74 280Z M136 214Q151 196 166 214V280Q151 292 136 280Z M72 292H168" fill="none" stroke="var(--au-text-3)" strokeWidth="3"/>
        <path d="M26 108V166M214 108V166M26 310V360M214 310V360" stroke="var(--au-text-3)" strokeWidth="10" strokeLinecap="round"/>
      </svg>
      <div>{fields.map(([name,key])=><div className="au-vehicle-reading" key={key}><span>{name}</span><strong>{text(vehicle[key])}</strong></div>)}</div>
    </div><div className="au-stage-caption">车辆俯视示意 · 读数来自当前车况观测</div>
  </section>
}
function MediaStage({message,onAction}:{message?:Msg;onAction?:(text:string)=>void}) {
  const payload=message?.actions?.find(a=>a.type.startsWith('media.control'))?.payload
  const title=typeof payload?.name==='string'?payload.name:typeof payload?.title==='string'?payload.title:'媒体信息读不到'
  return <section className="au-media-stage"><div className="au-media-cover"><Icon name="media" size={112} /></div>
    <h2>{title}</h2><div className="au-media-controls">{['上一首','暂停播放','下一首'].map(t=><button key={t} disabled={!onAction} onClick={()=>onAction?.(t)}>{t}</button>)}</div>
  </section>
}
