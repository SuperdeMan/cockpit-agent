// mobile/src/features/settings/voiceGrid.ts
// 设置页的两处排版判据（纯函数，零 RN import）：
//  · labelUnits：一段文案占几个「汉字宽」——中日韩字算 1，西文与数字算半个（单选行据此决定用分段按钮还是胶囊换行）；
//  · voiceGridColumns：音色格一行放几格。Figma S-2 按两字名（冰糖 / 茉莉）画的是 92dp 三列；MiniMax 引擎的名字
//    有四个字（甜美女声 / 青涩青年），三列在 360dp 外屏上只放得下两个字（2026-10-03 真机：「甜美…」）。
//    ⇒ 列数按**量到的宽度**与**最长的名字**算：放得下就三列，放不下退两列，再不行一列。

export function labelUnits(label: string): number {
  let n = 0
  for (const ch of label) n += (ch.codePointAt(0) ?? 0) >= 0x2e80 ? 1 : 0.5 // 0x2E80 起是中日韩部首、汉字与全角符号
  return n
}

/** 一格的固定开销（dp）：左右内边距 10×2 + 描边 1×2 + 图标 18 + 图文间距 8 */
const TILE_CHROME = 20 + 2 + 18 + 8
/** 格间距（dp），与 VoiceGrid 的 gap 同值 */
export const VOICE_GRID_GAP = 8

export function voiceGridColumns(width: number, names: readonly string[], fontSize: number): number {
  if (!(width > 0)) return 3 // 还没量到宽度（首帧 / 测试环境）：沿用设计稿的三列
  const longest = Math.max(1, ...names.map(labelUnits))
  // 西文字比半个汉字略宽（Figma 量过：13sp 的「Chloe」36dp，按半格算只有 32.5），整体留 10% 余量
  const tile = TILE_CHROME + Math.ceil(longest * fontSize * 1.1)
  return Math.max(1, Math.min(3, Math.floor((width + VOICE_GRID_GAP) / (tile + VOICE_GRID_GAP))))
}
