// A-8 图标集未覆盖的补充图标（Agent / 行程停靠 / 地点），照同一规格手绘：
// 24×24 viewBox · 1.8px stroke（由 Icon.tsx 的 svg 统一施加）· round cap/join · currentColor。
// 后续将经 use_figma 推回 Figma A-8 页，保持设计源一致（见 docs/design 实施计划）。
// 形态取标准线性图标语汇，与 Figma 导出的 39 个视觉一致。
export const ICON_CUSTOM = {
  // Shared rating data icons (visual v2 I4).
  star: { w: 24, h: 24, body: '<path d="M12 3.5 L14.41 9.58 L20.94 10 L15.9 14.17 L17.53 20.5 L12 17 L6.47 20.5 L8.1 14.17 L3.06 10 L9.59 9.58 Z"/>' },
  'star-filled': { w: 24, h: 24, body: '<path d="M12 3.5 L14.41 9.58 L20.94 10 L15.9 14.17 L17.53 20.5 L12 17 L6.47 20.5 L8.1 14.17 L3.06 10 L9.59 9.58 Z" fill="currentColor"/>' },
  'star-half': {
    w: 24,
    h: 24,
    body: '<path d="M12 3.5 L9.59 9.58 L3.06 10 L8.1 14.17 L6.47 20.5 L12 17 Z" fill="currentColor" stroke="none"/><path d="M12 3.5 L14.41 9.58 L20.94 10 L15.9 14.17 L17.53 20.5 L12 17 L6.47 20.5 L8.1 14.17 L3.06 10 L9.59 9.58 Z"/>',
  },
  // Visual v2 I4: Figma weather/data icons and promoted shared library icons.
  camera: { w: 24, h: 24, body: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>' },
  bolt: { w: 24, h: 24, body: '<path d="M4 14a1 1 0 0 1-.8-1.6l9-12A.5.5 0 0 1 13 .7V9h7a1 1 0 0 1 .8 1.6l-9 12a.5.5 0 0 1-.9-.3V14z"/>' },
  'chevron-down': { w: 24, h: 24, body: '<path d="M6 9l6 6 6-6"/>' },
  'chevron-up': { w: 24, h: 24, body: '<path d="M6 15l6-6 6 6"/>' },
  close: { w: 24, h: 24, body: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>' },
  trash: {
    w: 24,
    h: 24,
    body: '<path d="M4 6.5h16"/><path d="M9 6.5V4.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4.5v2"/><path d="M18.5 6.5l-.9 12.6a2 2 0 0 1-2 1.9H8.4a2 2 0 0 1-2-1.9L5.5 6.5"/><path d="M10 11v5.5"/><path d="M14 11v5.5"/>',
  },
  'external-link': { w: 24, h: 24, body: '<path d="M18 13.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5.5"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/>' },
  football: {
    w: 24,
    h: 24,
    body: '<circle cx="12" cy="12" r="9"/><path d="M12 8.6 L15.23 10.95 L14 14.75 L10 14.75 L8.77 10.95 Z"/><path d="M12 8.6 L12 3.1"/><path d="M15.23 10.95 L20.46 9.25"/><path d="M14 14.75 L17.23 19.2"/><path d="M10 14.75 L6.77 19.2"/><path d="M8.77 10.95 L3.54 9.25"/>',
  },
  lock: { w: 24, h: 24, body: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/><path d="M12 15v2.5"/>' },
  layers: { w: 24, h: 24, body: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 12.5l9 5 9-5"/><path d="M3 16.5l9 5 9-5"/>' },
  trophy: {
    w: 24,
    h: 24,
    body: '<path d="M8 3.5h8V9a4 4 0 0 1-8 0V3.5z"/><path d="M8 5.5H5.5a2.5 2.5 0 0 0 2.6 3.9"/><path d="M16 5.5h2.5a2.5 2.5 0 0 1-2.6 3.9"/><path d="M12 13v4"/><path d="M8.5 20.5h7"/><path d="M9.5 17h5v3.5h-5z"/>',
  },
  "weather-snow": {w:24,h:24,body:"\n<path d=\"M4.49999 13.5H17C17.9283 13.5663 18.8448 13.2611 19.5481 12.6516C20.2514 12.0421 20.6837 11.1782 20.75 10.25C20.8163 9.32172 20.5111 8.40514 19.9016 7.70188C19.2922 6.99861 18.4283 6.56628 17.5 6.49997C17.3635 5.37469 16.8487 4.32918 16.04 3.53488C15.2313 2.74059 14.1767 2.24466 13.0491 2.12842C11.9216 2.01218 10.788 2.28252 9.83423 2.89511C8.88048 3.50771 8.1632 4.4262 7.79999 5.49997C6.73913 5.06237 5.54787 5.06411 4.48829 5.50482C3.42871 5.94553 2.5876 6.78911 2.14999 7.84997C1.71239 8.91084 1.71413 10.1021 2.15484 11.1617C2.59555 12.2213 3.43913 13.0624 4.49999 13.5Z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M8 17.5V17.51M12 17.5V17.51M16 17.5V17.51M10 21V21.01M14 21V21.01\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n"},
  "weather-fog": {w:24,h:24,body:"\n<g clip-path=\"url(#clip0_23_310)\">\n<path d=\"M4.50001 11H17C17.9283 11.0663 18.8448 10.7611 19.5481 10.1516C20.2514 9.54213 20.6837 8.67823 20.75 7.74997C20.8163 6.82172 20.5112 5.90514 19.9017 5.20188C19.2922 4.49861 18.4283 4.06628 17.5 3.99997C17.3635 2.87469 16.8487 1.82918 16.04 1.03488C15.2313 0.240591 14.1767 -0.255338 13.0491 -0.371582C11.9216 -0.487825 10.788 -0.217483 9.83424 0.395114C8.8805 1.00771 8.16321 1.9262 7.80001 2.99997C6.73914 2.56237 5.54789 2.56411 4.48831 3.00482C3.42873 3.44553 2.58762 4.28911 2.15001 5.34997C1.7124 6.41084 1.71415 7.60209 2.15486 8.66167C2.59557 9.72126 3.43914 10.5624 4.50001 11Z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M3 15H21M5 19H19\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n</g>\n<defs>\n<clipPath id=\"clip0_23_310\">\n<rect width=\"24\" height=\"24\" fill=\"white\"/>\n</clipPath>\n</defs>\n"},
  "weather-haze": {w:24,h:24,body:"\n<path d=\"M12 12C13.933 12 15.5 10.433 15.5 8.5C15.5 6.567 13.933 5 12 5C10.067 5 8.5 6.567 8.5 8.5C8.5 10.433 10.067 12 12 12Z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M12 2V3M6.3 4.3L7 5M17.7 4.3L17 5M3 15H21M6 19H18\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n"},
  "weather-dust": {w:24,h:24,body:"\n<path d=\"M3 8H14C14.5933 8 15.1734 7.82405 15.6667 7.49441C16.1601 7.16477 16.5446 6.69623 16.7716 6.14805C16.9987 5.59987 17.0581 4.99667 16.9424 4.41473C16.8266 3.83279 16.5409 3.29824 16.1213 2.87868C15.7018 2.45912 15.1672 2.1734 14.5853 2.05765C14.0033 1.94189 13.4001 2.0013 12.8519 2.22836C12.3038 2.45543 11.8352 2.83994 11.5056 3.33329C11.1759 3.82664 11 4.40666 11 5\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M3 12H19C19.5933 12 20.1734 12.1759 20.6667 12.5056C21.1601 12.8352 21.5446 13.3038 21.7716 13.8519C21.9987 14.4001 22.0581 15.0033 21.9424 15.5853C21.8266 16.1672 21.5409 16.7018 21.1213 17.1213C20.7018 17.5409 20.1672 17.8266 19.5853 17.9424C19.0033 18.0581 18.4001 17.9987 17.8519 17.7716C17.3038 17.5446 16.8352 17.1601 16.5056 16.6667C16.1759 16.1734 16 15.5933 16 15\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M3 16H10M15 20H15.01M19 19.5H19.01M7 20H7.01\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n"},
  "trend": {w:24,h:24,body:"\n<path d=\"M3 17L9 11L13 15L21 7\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M15 7H21V13\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n"},
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
"driving": { w: 24, h: 24, body: "<path d=\"M12 21C16.9706 21 21 16.9706 21 12C21 7.02944 16.9706 3 12 3C7.02944 3 3 7.02944 3 12C3 16.9706 7.02944 21 12 21Z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M12 14.5C13.3807 14.5 14.5 13.3807 14.5 12C14.5 10.6193 13.3807 9.5 12 9.5C10.6193 9.5 9.5 10.6193 9.5 12C9.5 13.3807 10.6193 14.5 12 14.5Z\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n<path d=\"M12 14.5V21M9.60005 11.3L3.30005 9.59998M14.4 11.3L20.7001 9.59998\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>" },
"developer": { w: 24, h: 24, body: "<path d=\"M8 8L4 12L8 16M16 8L20 12L16 16M14 5L10 19\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>" },
  // ── 自 mobile icons.local 提升（2026-10-09）：mobile 与 dashboard 两端共用；Android Visual v3 补画，24 盒、1.8 描边 ──
  'arrow-left': { w: 24, h: 24, body: '<path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/>' },
  'arrow-right': { w: 24, h: 24, body: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>' },
  'more-vertical': { w: 24, h: 24, body: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>' },
  copy: { w: 24, h: 24, body: '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2"/><path d="M15.5 5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8.5a2 2 0 0 0 2 2"/>' },
  'car-window': { w: 24, h: 24, body: '<path d="M3 19h18"/><path d="M5 16V11l4.5-5H19v10H5z"/><path d="M13 6v10"/>' },
  trunk: { w: 24, h: 24, body: '<path d="M3.5 14h17v3.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V14z"/><path d="M5 14l1.6-4h10.8L19 14"/><path d="M12 7V2.5"/><path d="M9.5 5L12 2.5 14.5 5"/>' },
} as const

export type CustomIconName = keyof typeof ICON_CUSTOM
