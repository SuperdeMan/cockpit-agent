// 待机场景车况纯逻辑（node 可测；ContextualStage.tsx 只渲染）。
// 数据源：edge-gateway 的 vehicle_state WS 消息（NATS 车辆状态镜像，连上即推 + 变更广播）。

// PoC：VAL 暂无续航信号，按满电续航 × 电量折算；镜像里出现 range_km 信号时优先直用。
export const RANGE_FULL_KM = 550

function num(v) {
  const n = typeof v === 'string' ? parseFloat(v) : v
  return typeof n === 'number' && Number.isFinite(n) ? n : null
}

/**
 * 车况镜像 → 待机场景三格指标（电量/续航/挡位）。
 * 缺数据显示 '--'（页面刚开、网关镜像未就绪时的诚实占位，不再假装 62%/430km/P）。
 */
export function stageMetrics(state) {
  const s = state && typeof state === 'object' ? state : {}
  const battery = num(s.battery)
  const rangeKm = num(s.range_km) ?? (battery == null ? null : Math.round((battery / 100) * RANGE_FULL_KM))
  const gear = typeof s.gear === 'string' && s.gear ? s.gear : null
  return [
    { label: '电量', value: battery == null ? '--' : String(Math.round(battery)), unit: '%' },
    { label: '续航', value: rangeKm == null ? '--' : String(rangeKm), unit: 'km' },
    { label: '挡位', value: gear ?? '--', unit: '' },
  ]
}
