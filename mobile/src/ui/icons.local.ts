// mobile/src/ui/icons.local.ts
// mobile 专有图标数据（B4-9）。共享图标库（hmi icons.gen / icons.custom）里没有「发送」「键盘」，而 hmi/ 不碰、
// 共享台账不为 mobile 单方需求扩——本地放两枚，格式与 icons.custom.ts 同（24×24 盒、1.8 stroke、currentColor 由 Icon.tsx 替换）。
// arrowUp / stop：发送与打断**合一键**的两态（B5-13，泓舟 B4 真机轮原话②；替掉 B4-9 的纸飞机 send）。
// keyboard：行车档 B 身份「文本输入折叠成键盘图标」（§6.0）。
export const LOCAL_ICONS = {
  /** 发送（B5-13：⬆ 箭头，市面 AI 助手通行做法；替掉 B4-9 的纸飞机） */
  arrowUp: { w: 24, h: 24, body: '<path d="M12 19V5"/><path d="M5 12l7-7 7 7"/>' },
  /** 打断 / 停（B5-13：与发送合一键，忙时显示） */
  stop: { w: 24, h: 24, body: '<rect x="6" y="6" width="12" height="12" rx="2"/>' },
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
} as const

export type LocalIconName = keyof typeof LOCAL_ICONS
