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

  /** 看图问答角标（气泡 / 语音层转写前缀） */

  /** 补电 / 充电（充电路线卡、行程卡的充电停靠） */

  /** 完成（无需补电） */

  /** 待办方框（提醒段 / 提醒卡） */
  square: { w: 24, h: 24, body: '<rect width="18" height="18" x="3" y="3" rx="2"/>' },
  // ── Android Visual v3（方向 B）补画的 23 枚：Figma「小舟随行 · Android Visual v3」03 Components 图标板逐值回写，
  //    同规格（24 盒、1.8 stroke、圆头圆角）。前 15 枚给组件用（展开收起、返回、关闭、外链、复制、电话、星级、足球、删除），
  //    后 8 枚给车控结果 / 场景 / 赛事卡用。共享台账没有同名项（iconsLocal.test 守），本地合并在最后不覆盖谁。

  'chevron-up': { w: 24, h: 24, body: '<path d="M6 15l6-6 6 6"/>' },
  'arrow-left': { w: 24, h: 24, body: '<path d="M19 12H5"/><path d="M11 18l-6-6 6-6"/>' },
  'arrow-right': { w: 24, h: 24, body: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>' },

  close: { w: 24, h: 24, body: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>' },
  'more-vertical': { w: 24, h: 24, body: '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>' },

  copy: { w: 24, h: 24, body: '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2"/><path d="M15.5 5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v8.5a2 2 0 0 0 2 2"/>' },
  phone: { w: 24, h: 24, body: '<path d="M5 4h3.2l1.8 4.6-2.3 1.4a11.5 11.5 0 0 0 6.3 6.3l1.4-2.3L20 15.8V19a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>' },




  trash: {
    w: 24,
    h: 24,
    body: '<path d="M4 6.5h16"/><path d="M9 6.5V4.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4.5v2"/><path d="M18.5 6.5l-.9 12.6a2 2 0 0 1-2 1.9H8.4a2 2 0 0 1-2-1.9L5.5 6.5"/><path d="M10 11v5.5"/><path d="M14 11v5.5"/>',
  },
  'car-window': { w: 24, h: 24, body: '<path d="M3 19h18"/><path d="M5 16V11l4.5-5H19v10H5z"/><path d="M13 6v10"/>' },
  trunk: { w: 24, h: 24, body: '<path d="M3.5 14h17v3.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V14z"/><path d="M5 14l1.6-4h10.8L19 14"/><path d="M12 7V2.5"/><path d="M9.5 5L12 2.5 14.5 5"/>' },


  unlock: { w: 24, h: 24, body: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 7.6-1.8"/><path d="M12 15v2.5"/>' },



} as const

export type LocalIconName = keyof typeof LOCAL_ICONS
