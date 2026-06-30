// A-8 图标集未覆盖的补充图标（Agent / 行程停靠 / 地点），照同一规格手绘：
// 24×24 viewBox · 1.8px stroke（由 Icon.tsx 的 svg 统一施加）· round cap/join · currentColor。
// 后续将经 use_figma 推回 Figma A-8 页，保持设计源一致（见 docs/design 实施计划）。
// 形态取标准线性图标语汇，与 Figma 导出的 39 个视觉一致。
export const ICON_CUSTOM = {
  // ── Agent 能力（10）──
  vehicle: { w: 24, h: 24, body: '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.5 2.9A3 3 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>' },
  media: { w: 24, h: 24, body: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>' },
  compass: { w: 24, h: 24, body: '<path d="m16.2 7.8-1.8 5.4a2 2 0 0 1-1.2 1.3L7.8 16.2l1.8-5.4a2 2 0 0 1 1.2-1.3z"/><circle cx="12" cy="12" r="10"/>' },
  info: { w: 24, h: 24, body: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>' },
  itinerary: { w: 24, h: 24, body: '<path d="M14.1 5.6a2 2 0 0 0 1.8 0l3.6-1.8A1 1 0 0 1 21 4.6v12.8a1 1 0 0 1-.6.9l-4.5 2.3a2 2 0 0 1-1.8 0l-4.2-2.1a2 2 0 0 0-1.8 0l-3.6 1.8A1 1 0 0 1 3 19.4V6.6a1 1 0 0 1 .6-.9l4.5-2.3a2 2 0 0 1 1.8 0z"/><path d="M15 5.8v15"/><path d="M9 3.2v15"/>' },
  research: { w: 24, h: 24, body: '<path d="M14 2v6a2 2 0 0 0 .2 1l5.5 10a2 2 0 0 1-1.7 3H6a2 2 0 0 1-1.8-3l5.6-10a2 2 0 0 0 .2-1V2"/><path d="M6.5 15h11"/><path d="M8.5 2h7"/>' },
  dining: { w: 24, h: 24, body: '<path d="M3 2v7c0 1.1.9 2 2 2h1a2 2 0 0 0 2-2V2"/><path d="M5.5 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>' },
  parking: { w: 24, h: 24, body: '<rect width="18" height="18" x="3" y="3" rx="3"/><path d="M9 17V7h4a3 3 0 0 1 0 6H9"/>' },
  manual: { w: 24, h: 24, body: '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>' },
  chat: { w: 24, h: 24, body: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>' },
  // ── 行程停靠 / 路线标记 ──
  landmark: { w: 24, h: 24, body: '<path d="M10 18v-7"/><path d="M11.1 2.2a2 2 0 0 1 1.8 0l7.9 3.8c.5.3.3 1-.2 1H3.5c-.5 0-.7-.7-.2-1z"/><path d="M14 18v-7"/><path d="M18 18v-7"/><path d="M3 22h18"/><path d="M6 18v-7"/>' },
  hotel: { w: 24, h: 24, body: '<path d="M2 4v16"/><path d="M2 9h18a2 2 0 0 1 2 2v9"/><path d="M2 17h20"/><path d="M6 9v8"/><circle cx="8.5" cy="6.5" r="1.5"/>' },
  flag: { w: 24, h: 24, body: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>' },
  pin: { w: 24, h: 24, body: '<path d="M20 10c0 5-5.5 10.2-7.4 11.8a1 1 0 0 1-1.2 0C9.5 20.2 4 15 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/>' },
  // ── 地点 ──
  building: { w: 24, h: 24, body: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4M10 10h4M10 14h4M10 18h4"/>' },
  school: { w: 24, h: 24, body: '<path d="M21.4 10.9a1 1 0 0 0 0-1.8L12.8 5.2a2 2 0 0 0-1.6 0L2.6 9.1a1 1 0 0 0 0 1.8l8.6 3.9a2 2 0 0 0 1.6 0z"/><path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>' },
  // ── 补 A-8 集缺的通用图标（搜索/新闻/时效/完成/设置）——同 lucide 线性规格 ──
  search: { w: 24, h: 24, body: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>' },
  newspaper: { w: 24, h: 24, body: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8z"/>' },
  clock: { w: 24, h: 24, body: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>' },
  'check-circle': { w: 24, h: 24, body: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>' },
  settings: { w: 24, h: 24, body: '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>' },
} as const

export type CustomIconName = keyof typeof ICON_CUSTOM
