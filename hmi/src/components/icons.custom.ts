// A-8 图标集未覆盖的补充图标（Agent / 行程停靠 / 地点），照同一规格手绘：
// 24×24 viewBox · 1.8px stroke（由 Icon.tsx 的 svg 统一施加）· round cap/join · currentColor。
// 后续将经 use_figma 推回 Figma A-8 页，保持设计源一致（见 docs/design 实施计划）。
// 形态取标准线性图标语汇，与 Figma 导出的 39 个视觉一致。
export const ICON_CUSTOM = {
  // Visual v2 I3: exact shared dialog/control icon paths.
  refresh: { w: 24, h: 24, body: '<path d="M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.7-2.8L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.7 2.8L21 8"/><path d="M21 3v5h-5"/>' },
  check: { w: 24, h: 24, body: '<path d="M20 6 9 17l-5-5"/>' },
  'arrow-down': { w: 24, h: 24, body: '<path d="M12 5v14"/><path d="M6 13l6 6 6-6"/>' },
  'seat-heat': {
    w: 24,
    h: 24,
    body: '<path d="M6.5 4.5A1.5 1.5 0 0 1 8 3h1.5A1.5 1.5 0 0 1 11 4.5V13h5a2 2 0 0 1 2 2v2H8a1.5 1.5 0 0 1-1.5-1.5v-11z"/><path d="M9 17v3.5"/><path d="M16 17v3.5"/><path d="M14.5 3.5c-1 1 1 2 0 3s1 2 0 3"/><path d="M18 3.5c-1 1 1 2 0 3s1 2 0 3"/>',
  },
  snowflake: { w: 24, h: 24, body: '<path d="M12 2.5v19"/><path d="M3.8 7.25l16.4 9.5"/><path d="M3.8 16.75l16.4-9.5"/><path d="M9.6 4.2L12 6l2.4-1.8"/><path d="M9.6 19.8L12 18l2.4 1.8"/>' },
  // Visual v2 I2: Figma privacy vectors (28 -> registry 24); shared mobile send/stop.
  'privacy-mic': { w: 24, h: 24, body: '<g transform="scale(0.8571428571)"><g><path d="M13.4167 3.5C15.3497 3.5 16.9167 5.067 16.9167 7V12.8333C16.9167 14.7663 15.3497 16.3333 13.4167 16.3333C11.4837 16.3333 9.91667 14.7663 9.91667 12.8333V7C9.91667 5.067 11.4837 3.5 13.4167 3.5Z" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/><path d="M5.25 12.8333C5.25 14.9993 6.11041 17.0765 7.64196 18.608C9.17351 20.1396 11.2507 21 13.4167 21C15.5826 21 17.6598 20.1396 19.1914 18.608C20.7229 17.0765 21.5833 14.9993 21.5833 12.8333" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/><path d="M13.4167 21V24.5" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/><path d="M23.3333 7.58333C24.622 7.58333 25.6667 6.53867 25.6667 5.25C25.6667 3.96134 24.622 2.91667 23.3333 2.91667C22.0447 2.91667 21 3.96134 21 5.25C21 6.53867 22.0447 7.58333 23.3333 7.58333Z" fill="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/></g></g>' },
  'privacy-cloud': { w: 24, h: 24, body: '<g transform="scale(0.8571428571)"><g><path d="M8.16667 21H7C5.76232 21.0774 4.54461 20.6599 3.61474 19.8394C2.68487 19.0189 2.11902 17.8627 2.04167 16.625C1.96431 15.3873 2.38179 14.1696 3.20226 13.2397C4.02273 12.3099 5.17899 11.744 6.41667 11.6667C6.56975 10.0894 7.25347 8.6108 8.35601 7.47259C9.45855 6.33437 10.9147 5.60393 12.4862 5.40071C14.0578 5.19749 15.6518 5.53352 17.0076 6.35385C18.3634 7.17417 19.4008 8.43025 19.95 9.91667C21.4197 9.93214 22.8231 10.5308 23.8515 11.581C24.8798 12.6312 25.4488 14.0469 25.4333 15.5167C25.4179 16.9864 24.8192 18.3898 23.769 19.4181C22.7188 20.4465 21.3031 21.0155 19.8333 21H18.6667" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/><path d="M14 14V23.3333M17.5 17.5L14 14L10.5 17.5" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/></g></g>' },
  'privacy-camera': { w: 24, h: 24, body: '<g transform="scale(0.8571428571)"><g><path d="M3.5 9.33333C3.5 8.7145 3.74583 8.121 4.18342 7.68342C4.621 7.24583 5.2145 7 5.83333 7H8.16667L9.91667 4.66667H18.0833L19.8333 7H22.1667C22.7855 7 23.379 7.24583 23.8166 7.68342C24.2542 8.121 24.5 8.7145 24.5 9.33333V21C24.5 21.6188 24.2542 22.2123 23.8166 22.6499C23.379 23.0875 22.7855 23.3333 22.1667 23.3333H5.83333C5.2145 23.3333 4.621 23.0875 4.18342 22.6499C3.74583 22.2123 3.5 21.6188 3.5 21V9.33333Z" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/><path d="M18.0833 15.1667C18.0833 17.4218 16.2552 19.25 14 19.25C11.7448 19.25 9.91667 17.4218 9.91667 15.1667C9.91667 12.9115 11.7448 11.0833 14 11.0833C16.2552 11.0833 18.0833 12.9115 18.0833 15.1667Z" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/><path d="M21 11.0833C21.3222 11.0833 21.5833 10.8222 21.5833 10.5C21.5833 10.1778 21.3222 9.91667 21 9.91667C20.6778 9.91667 20.4167 10.1778 20.4167 10.5C20.4167 10.8222 20.6778 11.0833 21 11.0833Z" fill="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" stroke="currentColor"/></g></g>' },
  arrowUp: { w: 24, h: 24, body: '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>' },
  stop: { w: 24, h: 24, body: '<rect x="6" y="6" width="12" height="12" rx="2"/>' },
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
