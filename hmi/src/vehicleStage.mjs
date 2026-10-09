// 待机场景车况纯逻辑（node 可测；ContextualStage.tsx 只渲染）。
// 数据源：edge-gateway 的 vehicle_state WS 消息（NATS 车辆状态镜像，连上即推 + 变更广播）。

function num(v) {
  const n = typeof v === 'string' ? parseFloat(v) : v
  return typeof n === 'number' && Number.isFinite(n) ? n : null
}

/**
 * 车况镜像 → 待机场景三格指标（电量/续航/挡位）。
 * 缺失读数明确显示读不到；不把电量换算成未经观测的续航。
 */
export function stageMetrics(state) {
  const s = state && typeof state === 'object' ? state : {}
  const battery = num(s.battery)
  const rangeKm = num(s.range_km)
  const gear = typeof s.gear === 'string' && s.gear ? s.gear : null
  return [
    { label: '电量', value: battery == null ? '读不到' : String(Math.round(battery)), unit: battery == null ? '' : '%' },
    { label: '续航', value: rangeKm == null ? '读不到' : String(rangeKm), unit: rangeKm == null ? '' : 'km' },
    { label: '挡位', value: gear ?? '读不到', unit: '' },
  ]
}
