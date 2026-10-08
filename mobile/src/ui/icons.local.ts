// mobile/src/ui/icons.local.ts
// mobile 专有图标数据（B4-9）。共享图标库（hmi icons.gen / icons.custom）里没有「发送」「键盘」，而 hmi/ 不碰、
// 共享台账不为 mobile 单方需求扩——本地放两枚，格式与 icons.custom.ts 同（24×24 盒、1.8 stroke、currentColor 由 Icon.tsx 替换）。
// arrowUp / stop：发送与打断**合一键**的两态（B5-13，泓舟 B4 真机轮原话②；替掉 B4-9 的纸飞机 send）。
// keyboard：行车档 B 身份「文本输入折叠成键盘图标」（§6.0）。
export const LOCAL_ICONS = {
  /** 发送（B5-13：⬆ 箭头，市面 AI 助手通行做法；替掉 B4-9 的纸飞机） */
  /** 打断 / 停（B5-13：与发送合一键，忙时显示） */
  keyboard: {
    w: 24,
    h: 24,
    body: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10"/>',
  },
  // ── 打磨批 C（评审 P15）：emoji 不再当图标。评审点名的八枚里 warning（icons.gen）与 chat / clock / pin（icons.custom）
  //    共享台账已有（直接复用，本地不再画一份——同名会被 Icon.tsx 的合并顺序静默覆盖），
  //    这里只补真缺的：⟳ → refresh、📷 → camera、⚡ → bolt、✅ → check、☐ → square。同 lucide 线性规格。
  /** 处理中（Dock 长任务、过程区折叠条） */
  refresh: { w: 24, h: 24, body: '<path d="M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.7-2.8L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.7 2.8L21 8"/><path d="M21 3v5h-5"/>' },
  /** 看图问答角标（气泡 / 语音层转写前缀） */
  camera: { w: 24, h: 24, body: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>' },
  /** 补电 / 充电（充电路线卡、行程卡的充电停靠） */
  bolt: { w: 24, h: 24, body: '<path d="M4 14a1 1 0 0 1-.8-1.6l9-12A.5.5 0 0 1 13 .7V9h7a1 1 0 0 1 .8 1.6l-9 12a.5.5 0 0 1-.9-.3V14z"/>' },
  /** 完成（无需补电） */
  check: { w: 24, h: 24, body: '<path d="M20 6 9 17l-5-5"/>' },
  /** 待办方框（提醒段 / 提醒卡） */
  square: { w: 24, h: 24, body: '<rect width="18" height="18" x="3" y="3" rx="2"/>' },
  // ── Android Visual v3（方向 B）补画的 23 枚：Figma「小舟随行 · Android Visual v3」03 Components 图标板逐值回写，
  //    同规格（24 盒、1.8 stroke、圆头圆角）。前 15 枚给组件用（展开收起、返回、关闭、外链、复制、电话、星级、足球、删除），
  //    后 8 枚给车控结果 / 场景 / 赛事卡用。共享台账没有同名项（iconsLocal.test 守），本地合并在最后不覆盖谁。
  'chevron-down': { w: 24, h: 24, body: '<path d="M6 9l6 6 6-6"/>' },
  'chevron-up': { w: 24, h: 24, body: '<path d="M6 15l6-6 6 6"/>' },
  'arrow-left': { w: 24, h: 24, body: '<path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/>' },
  'arrow-right': { w: 24, h: 24, body: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>' },
  'arrow-down': { w: 24, h: 24, body: '<path d="M12 5v14"/><path d="M6 13l6 6 6-6"/>' },
  close: { w: 24, h: 24, body: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>' },
  'more-vertical': { w: 24, h: 24, body: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>' },
  'external-link': { w: 24, h: 24, body: '<path d="M18 13.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5.5"/><path d="M15 3h6v6"/><path d="M10 14L21 3"/>' },
  copy: { w: 24, h: 24, body: '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2"/><path d="M15.5 5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8.5a2 2 0 0 0 2 2"/>' },
  phone: { w: 24, h: 24, body: '<path d="M5 4h3.2l1.8 4.6-2.3 1.4a11.5 11.5 0 0 0 6.3 6.3l1.4-2.3L20 15.8V19a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>' },
  star: { w: 24, h: 24, body: '<path d="M12 3.5 L14.41 9.58 L20.94 10 L15.9 14.17 L17.53 20.5 L12 17 L6.47 20.5 L8.1 14.17 L3.06 10 L9.59 9.58 Z"/>' },
  'star-filled': { w: 24, h: 24, body: '<path d="M12 3.5 L14.41 9.58 L20.94 10 L15.9 14.17 L17.53 20.5 L12 17 L6.47 20.5 L8.1 14.17 L3.06 10 L9.59 9.58 Z" fill="currentColor"/>' },
  'star-half': {
    w: 24,
    h: 24,
    body: '<path d="M12 3.5 L9.59 9.58 L3.06 10 L8.1 14.17 L6.47 20.5 L12 17 Z" fill="currentColor" stroke="none"/><path d="M12 3.5 L14.41 9.58 L20.94 10 L15.9 14.17 L17.53 20.5 L12 17 L6.47 20.5 L8.1 14.17 L3.06 10 L9.59 9.58 Z"/>',
  },
  football: {
    w: 24,
    h: 24,
    body: '<circle cx="12" cy="12" r="9"/><path d="M12 8.6 L15.23 10.95 L14 14.75 L10 14.75 L8.77 10.95 Z"/><path d="M12 8.6 L12 3.1"/><path d="M15.23 10.95 L20.46 9.25"/><path d="M14 14.75 L17.23 19.2"/><path d="M10 14.75 L6.77 19.2"/><path d="M8.77 10.95 L3.54 9.25"/>',
  },
  trash: {
    w: 24,
    h: 24,
    body: '<path d="M4 6.5h16"/><path d="M9 6.5V4.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4.5v2"/><path d="M18.5 6.5l-.9 12.6a2 2 0 0 1-2 1.9H8.4a2 2 0 0 1-2-1.9L5.5 6.5"/><path d="M10 11v5.5"/><path d="M14 11v5.5"/>',
  },
  'car-window': { w: 24, h: 24, body: '<path d="M3 19h18"/><path d="M5 16V11l4.5-5H19v10H5z"/><path d="M13 6v10"/>' },
  trunk: { w: 24, h: 24, body: '<path d="M3.5 14h17v3.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V14z"/><path d="M5 14l1.6-4h10.8L19 14"/><path d="M12 7V2.5"/><path d="M9.5 5L12 2.5 14.5 5"/>' },
  'seat-heat': {
    w: 24,
    h: 24,
    body: '<path d="M6.5 4.5A1.5 1.5 0 0 1 8 3h1.5A1.5 1.5 0 0 1 11 4.5V13h5a2 2 0 0 1 2 2v2H8a1.5 1.5 0 0 1-1.5-1.5v-11z"/><path d="M9 17v3.5"/><path d="M16 17v3.5"/><path d="M14.5 3.5c-1 1 1 2 0 3s1 2 0 3"/><path d="M18 3.5c-1 1 1 2 0 3s1 2 0 3"/>',
  },
  lock: { w: 24, h: 24, body: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/><path d="M12 15v2.5"/>' },
  unlock: { w: 24, h: 24, body: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 7.6-1.8"/><path d="M12 15v2.5"/>' },
  snowflake: { w: 24, h: 24, body: '<path d="M12 2.5v19"/><path d="M3.8 7.25l16.4 9.5"/><path d="M3.8 16.75l16.4-9.5"/><path d="M9.6 4.2L12 6l2.4-1.8"/><path d="M9.6 19.8L12 18l2.4 1.8"/>' },
  layers: { w: 24, h: 24, body: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 12.5l9 5 9-5"/><path d="M3 16.5l9 5 9-5"/>' },
  trophy: {
    w: 24,
    h: 24,
    body: '<path d="M8 3.5h8V9a4 4 0 0 1-8 0V3.5z"/><path d="M8 5.5H5.5a2.5 2.5 0 0 0 2.6 3.9"/><path d="M16 5.5h2.5a2.5 2.5 0 0 1-2.6 3.9"/><path d="M12 13v4"/><path d="M8.5 20.5h7"/><path d="M9.5 17h5v3.5h-5z"/>',
  },
} as const

export type LocalIconName = keyof typeof LOCAL_ICONS
