import { readBuildInfo, type BuildInfo } from './buildInfo'
import type { AppSettings } from './settings/store'

/** 操作诊断只属于显式 dev 变体；release/Metro 与变体是两件事，缺失配置不得放行。 */
export function developmentDiagnosticsEnabled(): boolean {
  return readBuildInfo().variant === 'dev'
}

/** 构建行连点几次解锁开发者选项（打磨批 B） */
export const DEVELOPER_UNLOCK_TAPS = 7

/** 设置页「开发者选项」显不显示——**唯一的一份**（打磨批 B，评审 P17 / D1 / D2）：
 *  非 prod 变体一直显示；prod 只在用户于构建行连点 DEVELOPER_UNLOCK_TAPS 次之后显示。
 *  它打开的只是**只读**的取证屏与画廊；会采集 / 播放 / 发送的操作诊断仍只认 dev 变体
 *  （developmentDiagnosticsEnabled），解锁改不了那一条。 */
export function developerOptionsVisible(settings: Pick<AppSettings, 'developerUnlocked'>, build: Pick<BuildInfo, 'variant'>): boolean {
  return build.variant !== 'prod' || settings.developerUnlocked === true
}
