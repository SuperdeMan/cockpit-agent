const isLatitude = (value) => Number.isFinite(value) && value >= -90 && value <= 90
const isLongitude = (value) => Number.isFinite(value) && value >= -180 && value <= 180

export function buildLocationMeta(location) {
  if (!location || !isLatitude(location.lat) || !isLongitude(location.lng)) return {}
  const accuracy = Number(location.accuracyM)
  const capturedAt = Number(location.capturedAt)
  return {
    current_lat: location.lat.toFixed(6),
    current_lng: location.lng.toFixed(6),
    ...(Number.isFinite(accuracy) && accuracy >= 0 ? { current_accuracy_m: String(Math.round(accuracy)) } : {}),
    ...(Number.isFinite(capturedAt) && capturedAt > 0 ? { current_location_at: String(Math.round(capturedAt)) } : {}),
    current_location_source: 'browser',
  }
}

export function buildRequestLocationMeta(enabled, location) {
  return enabled ? buildLocationMeta(location) : {}
}

const WEATHER_TERMS = /(天气|气温|温度|下雨|降雨|预报|空气质量|AQI|紫外线)/i
const LOCATION_DEPENDENT_TERMS = /(导航|带我去|怎么去|附近|周边|周围|充电站|停车场|找餐厅|找吃的|在哪|当前位置|我的位置|这是哪|我现在的位置|我的方位|充电|补电|续航|中途充|行程规划|规划行程|几日游|两日游|一日游|自驾)/
const EXPLICIT_PLACE = /(北京|上海|天津|重庆|深圳|广州|杭州|南京|苏州|成都|武汉|西安|郑州|长沙|青岛|厦门|福州|济南|合肥|昆明|贵阳|南宁|海口|石家庄|太原|沈阳|大连|长春|哈尔滨|呼和浩特|兰州|西宁|银川|乌鲁木齐|拉萨|香港|澳门|台湾|[\u4e00-\u9fff]{2,}(?:市|省|自治区|区|县|镇|村|路|街|机场|车站|广场|大厦|大学|医院|景区|公园))/

// 这条查询是否依赖"当前位置"（导航/就近/我在哪/无明确城市的天气）。
// 定位已开启时，业务应先刷新一次实时定位再发；未开启时则触发授权征询。
export function isLocationDependent(text) {
  const normalized = String(text || '').trim()
  if (!normalized) return false
  if (LOCATION_DEPENDENT_TERMS.test(normalized)) return true
  return WEATHER_TERMS.test(normalized) && !EXPLICIT_PLACE.test(normalized)
}

export function shouldRequestLocationConsent(text, locationEnabled) {
  if (locationEnabled) return false
  return isLocationDependent(text)
}

export function requestCurrentLocation() {
  if (!navigator.geolocation) return Promise.reject(new Error('unsupported'))
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(
      ({ coords, timestamp }) => resolve({
        lat: coords.latitude,
        lng: coords.longitude,
        accuracyM: coords.accuracy,
        capturedAt: timestamp,
      }),
      (error) => reject(error),
      { enableHighAccuracy: true, maximumAge: 30_000, timeout: 10_000 },
    )
  })
}
