import { readBuildInfo } from './buildInfo'

/** 操作诊断只属于显式 dev 变体；release/Metro 与变体是两件事，缺失配置不得放行。 */
export function developmentDiagnosticsEnabled(): boolean {
  return readBuildInfo().variant === 'dev'
}
