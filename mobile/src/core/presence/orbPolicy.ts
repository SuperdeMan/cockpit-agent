// Composer 主球要不要跑循环动画——唯一判据（B3-1）。
// 曾内联在 ChatScreen.tsx:530；抽出来是因为它已经有两个利益方（G5 性能 / S4 可读性），
// B4 的 reduce-motion（方案 §8：循环全部降静帧）将来也落这里，不再各写一份。
// ⚠ 它只回答「动不动画」；「静止时 speaking 还读不读得出」是 AuroraOrb 静态标记的事，
//   两个判据刻意分开——合成一个就会回到「要么动（破 G5）要么盲（S4）」的二选一。
import type { PresenceSnapshot } from './presence'

export function composerOrbAnimated(s: Pick<PresenceSnapshot, 'input'>): boolean {
  // 层开着：层内大球（VoiceSheet.tsx:168，animated 恒 true）接管那「1 个」循环动画
  return s.input !== 'voice-sheet'
}
