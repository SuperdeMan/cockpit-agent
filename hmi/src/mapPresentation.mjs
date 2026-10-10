// Read-only projection of existing card geometry. Backend coordinates are authoritative.
const record = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {}
const text = value => typeof value === 'string' ? value : ''
const list = value => Array.isArray(value) ? value : []

export function mapCoordinate(value) {
  const { lat, lng } = record(value)
  return Number.isFinite(lat) && Math.abs(lat) <= 90 && Number.isFinite(lng) && Math.abs(lng) <= 180 && (lat !== 0 || lng !== 0) ? [lng, lat] : null
}

export function projectMap(card) {
  const c = record(card)
  const route = c.type === 'route_plan' || c.type === 'charging_route'
  const rawPath = list(c.path)
  let path = rawPath.length > 1 && rawPath.every(p => Array.isArray(p) && p.length === 2 && mapCoordinate({lat:p[0],lng:p[1]}))
    ? rawPath.map(([lat,lng]) => [lng,lat]) : []
  // Match the existing client geometry cap while retaining both endpoints.
  if (path.length > 400) path = Array.from({length:400},(_,i)=>path[Math.round(i*(path.length-1)/399)])
  let entries = []
  if (c.type === 'poi_list' || c.type === 'place_list') entries = list(c.items).map((item,index) => {
    const p = record(item)
    return { name: text(p.name), label: String(index+1), position: mapCoordinate(p), detail: text(p.address) }
  })
  else if (c.type === 'poi_detail' || c.type === 'place_detail') entries = [{ name:text(c.name), label:'1', position:mapCoordinate(c), detail:text(c.address) }]
  else if (c.type === 'trip_itinerary') entries = list(c.itinerary).flatMap(day => list(record(day).stops).map(stop => {
    const s = record(stop)
    return { name:text(s.name), label:'', position:s.grounded === true ? mapCoordinate(s.poi) : null, detail:'第 '+record(day).day_index+' 天' }
  })).map((entry,index) => ({...entry,label:String(index+1)}))
  else if (route) {
    entries = [
      { name:text(c.origin)||'出发地', label:'起', position:mapCoordinate(c.origin_loc)||path[0]||null, detail:'' },
      ...list(c.type === 'charging_route' ? c.stops : c.waypoints).map((item,index) => {
        const p = record(item)
        return { name:text(p.name), label:String(index+1), position:mapCoordinate(p), detail:Number.isFinite(p.at_km)?'约 '+p.at_km+' km 处补电':text(p.address) }
      }),
      { name:text(c.destination)||'目的地', label:'终', position:mapCoordinate(c.destination_loc)||path.at(-1)||null, detail:'' },
    ]
  }
  const cancelled = c.cancelled === true
  if (cancelled) entries = []
  const markers = entries.map((entry,index) => ({...entry,index})).filter(entry => entry.position)
  return {
    entries, markers, path:cancelled?[]:path, cancelled, route,
    hasCoordinates:!cancelled && (markers.length > 0 || path.length > 1),
    missingCoordinates:entries.length-markers.length,
    pathMissing:route && !cancelled && path.length < 2,
    title:cancelled?'导航已取消':text(c.title)||text(c.destination)||text(c.name)||text(c.keyword)||'附近地点',
  }
}

// AMap's avoid order is top, bottom, left, right (not CSS shorthand).
export function mapViewPadding({width,height,panelRight=0,headerBottom=150,summaryHeight=0,answerHeight=0,driving=false}) {
  // 64px includes the largest touch target, selection ring and SDK pixel rounding.
  const left = !driving && width > 1200 ? Math.min(panelRight+64,width-360) : 64
  return [Math.min(headerBottom+28,height*.28), Math.min(Math.max(summaryHeight+64,answerHeight+40,96),height*.48), Math.max(64,left),64]
}
