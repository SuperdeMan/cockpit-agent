// mobile/src/core/session/quickCommands.ts
// 首页推荐筛选在 App 侧的绑定层：判据在共享 `quickCommands.mjs`，
// 声明（示例 → 能力、UI id → 服务端 agent_id）在共享 `types.ts`。
// 这里只把两者接起来并标类型——**不抄第二份表**。
import { AGENT_CATALOG, SYSTEM_QUICK_COMMAND_AGENTS } from '@shared/types.ts'
import { visibleQuickCommands as filterCommands } from '@shared/quickCommands.mjs'

import type { SessionSummary } from '../api/sessionInfo'

/** UI 目录 id → 服务端 Registry agent_id（缺省同名，差异写在 AGENT_CATALOG.serverId）。 */
export function serverAgentId(catalogId: string): string {
  return AGENT_CATALOG.find((a) => a.id === catalogId)?.serverId ?? catalogId
}

export function visibleQuickCommands(
  commands: readonly string[],
  summary: SessionSummary | null,
  enabledAgents: Record<string, boolean> | undefined,
): string[] {
  // 共享层的 JSDoc 声明的是可变数组；这里的入参只读，拷一份传进去（它本来也不改原数组）
  return filterCommands(
    [...commands], summary, enabledAgents, SYSTEM_QUICK_COMMAND_AGENTS, serverAgentId,
  ) as string[]
}
