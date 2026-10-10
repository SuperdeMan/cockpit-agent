let pending

export function loadMapSdk() {
  if (pending) return pending
  pending = (async () => {
    let config
    try {
      const response = await fetch('/api/maps/config', { signal:AbortSignal.timeout(8000), cache:'no-store' })
      if (!response.ok) throw new Error()
      config = await response.json()
    } catch { throw new Error('地图服务暂时不可用') }
    if (!config.available || typeof config.key !== 'string') throw new Error('地图尚未配置')
    window._AMapSecurityConfig = { serviceHost:window.location.origin+'/_AMapService' }
    if (window.AMap?.Map) return window.AMap
    return new Promise((resolve,reject) => {
      const script = document.createElement('script')
      const name = '__hmiMapSdkReady'
      let finished = false
      const finish = error => {
        if (finished) return
        finished = true
        clearTimeout(timer)
        delete window[name]
        script.onerror = null
        if (error) { script.remove(); reject(new Error('地图加载失败，请稍后重试')) }
        else resolve(window.AMap)
      }
      const timer = setTimeout(() => finish(true),15000)
      window[name] = () => finish(!window.AMap?.Map)
      script.onerror = () => finish(true)
      const url = new URL('https://webapi.amap.com/maps')
      url.search = new URLSearchParams({v:'2.0',key:config.key,callback:name}).toString()
      script.src = url.toString()
      script.referrerPolicy = 'strict-origin-when-cross-origin'
      document.head.append(script)
    })
  })().catch(error => { pending = undefined; throw error })
  return pending
}

export function createMapView(AMap, container, {model,theme,driving,onReady,onError,onSelect,padding}) {
  const color = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
  let disposed = false
  const map = new AMap.Map(container, {
    viewMode:'2D', resizeEnable:true, showIndoorMap:false, keyboardEnable:false, rotateEnable:false,
    animateEnable:!reduced, features:['bg','road'], zooms:[3,18], center:model.markers[0]?.position||model.path[0], zoom:13,
    mapStyle:theme === 'light'?'amap://styles/whitesmoke':'amap://styles/dark',
  })
  let overlays = []
  let markerNodes = []
  let selectedIndex = -1
  let timer = setTimeout(() => { if (!disposed) onError('地图底图加载超时') },18000)
  map.on('complete', () => { clearTimeout(timer); if (!disposed) onReady() })
  function fit() { if (!disposed && overlays.length) map.setFitView(overlays,true,padding(),16) }
  function select(index) {
    selectedIndex = index
    markerNodes.forEach(({entry,node}) => node.classList.toggle('selected',entry.index === index))
  }
  function draw(next) {
    model = next
    map.remove(overlays)
    overlays = []
    markerNodes = []
    if (model.path.length > 1) overlays.push(new AMap.Polyline({path:model.path,strokeColor:color('--au-map-route'),strokeWeight:8,strokeOpacity:1,
      borderWeight:3,isOutline:true,outlineColor:color('--au-map-land'),lineJoin:'round',lineCap:'round',zIndex:40}))
    for (const entry of model.markers) {
      const node = document.createElement('button')
      node.type = 'button'
      node.className = 'au-map-marker'
      node.textContent = entry.label
      node.title = entry.name
      node.setAttribute('aria-label',entry.label+' '+entry.name)
      node.addEventListener('click',() => { if (!driving) onSelect(entry.index) })
      const marker = new AMap.Marker({position:entry.position,content:node,anchor:'center',zIndex:60})
      overlays.push(marker)
      markerNodes.push({entry,node})
    }
    map.add(overlays)
    select(selectedIndex)
    fit()
  }
  function setDriving(value) {
    driving = value
    map.setStatus({dragEnable:!value,zoomEnable:!value,doubleClickZoom:!value,keyboardEnable:false})
    markerNodes.forEach(({node}) => { node.disabled = value; node.tabIndex = value?-1:0 })
    fit()
  }
  draw(model)
  setDriving(driving)
  return { fit, select, setDriving,
    setModel(next) { draw(next); setDriving(driving) },
    setTheme(value) { map.setMapStyle(value === 'light'?'amap://styles/whitesmoke':'amap://styles/dark'); draw(model); setDriving(driving) },
    destroy() { disposed = true; clearTimeout(timer); map.destroy(); overlays=[]; markerNodes=[] },
  }
}
