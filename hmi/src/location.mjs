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
// ── QA 卡 Q4：这道闸此前是一条大正则做子串匹配，四个缺陷叠在一起（I-007/I-032①）：
// 「打开**充电**口」命中 `充电`、「**取消**当前**导航**」命中 `导航`、显式给了地点的
// 「查深圳欢乐海岸周边停车场」仍被 `周边|停车场` 判成依赖当前定位，而闸又在 `send()`
// 里拦**整句**，多意图句里的车控/股票全部陪葬。
//
// 判据：**客户端不该做意图判定**。这道闸真正要问的是「本轮执行是否需要当前坐标」，
// 那个答案只有编排侧知道。所以闸只保留最保守的一档，认不准就放行——放行的代价是
// 后端诚实降级（「我现在拿不到车辆的位置」，E4 实证），拦错的代价是整句被吞。
//
// 收窄成三条同时成立：命中位置依赖词 + 没有可用的地点线索 + 不是否定/取消句。

// ORIGIN：要的是**出发地** = 当前位置。句中有目的地也不能豁免。
const ORIGIN_TERMS = /(导航|带我去|怎么去|送我去|开车去|中途充|补电|行程规划|规划行程|几日游|两日游|一日游|自驾)/
// ANCHOR：要的是一个**锚点**。句中给了显式地点，那个地点就是锚点，无需当前坐标。
const ANCHOR_TERMS = /(附近|周边|周围|最近的|充电站|充电桩|去充电|找充电|停车场|找餐厅|找吃的|在哪|当前位置|我的位置|这是哪|我现在的位置|我的方位)/
// 否定/取消：只认**紧邻**位置动作的否定（≤4 字间隔），不做全句否定词扫描——
// 「别忘了提醒我到公司充电站补电」里的「别」离得远，不是在否定那件事。
const NEGATED_LOCATION = /(取消|别|不要|不用|停止|关闭|结束|退出|终止|先不)[^，。；,;]{0,4}(导航|带我去|开始|充电|规划|去)/
// ⚠ 车控对象那半（「打开充电口」）由**收窄词表**解决——`充电` 这个裸词删了，
// 只留 `充电站|充电桩|去充电|找充电`。**刻意不再加一份「车控对象白名单」**：
// 两份声明表达同一件事迟早漂移（B4 那条判据）。`续航` 同理整个移出闸——
// 「续航还剩多少」是车况问题，为它去要定位正是 I-007 的形态。
const EXPLICIT_PLACE = /(北京|上海|天津|重庆|深圳|广州|杭州|南京|苏州|成都|武汉|西安|郑州|长沙|青岛|厦门|福州|济南|合肥|昆明|贵阳|南宁|海口|石家庄|太原|沈阳|大连|长春|哈尔滨|呼和浩特|兰州|西宁|银川|乌鲁木齐|拉萨|香港|澳门|台湾|[\u4e00-\u9fff]{2,}(?:市|省|自治区|区|县|镇|村|路|街|机场|车站|广场|大厦|大学|医院|景区|公园))/

/** 这条查询是否依赖"当前位置"。定位已开启时业务先刷新实时定位再发；未开启则触发授权征询。 */
export function isLocationDependent(text) {
  const normalized = String(text || '').trim()
  if (!normalized) return false
  if (NEGATED_LOCATION.test(normalized)) return false
  if (ORIGIN_TERMS.test(normalized)) return true
  const hasPlace = EXPLICIT_PLACE.test(normalized)
  if (ANCHOR_TERMS.test(normalized)) return !hasPlace
  return WEATHER_TERMS.test(normalized) && !hasPlace
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
