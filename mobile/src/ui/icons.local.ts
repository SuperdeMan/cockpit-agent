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
} as const

export type LocalIconName = keyof typeof LOCAL_ICONS
