// mobile/src/core/session/quickCommands.ts
// 首页推荐筛选在 App 侧的绑定层：判据在共享 `quickCommands.mjs`，
// 声明（示例 → 能力、UI id → 服务端 agent_id）在共享 `types.ts`。
// 这里只把两者接起来并标类型——**不抄第二份表**。
import { AGENT_CATALOG, SYSTEM_QUICK_COMMAND_AGENTS } from '@shared/types.ts'
import { visibleQuickCommands as filterCommands } from '@shared/quickCommands.mjs'

import type { SessionSummary } from '../api/sessionInfo'

/** 首页示例的 mobile 侧顺序（打磨批 A，裁决 J2 / 评审 D6）：**同一集合、只换顺序**——共享
 *  `DEFAULT_QUICK_COMMANDS` 前三条全是车控，手机档用户多数没有 `vehicle.control`；这里把
 *  跨能力的排在前（天气 / 附近充电站 / 讲个笑话 / 空调…）。集合与共享表逐项相同由单测钉住，
 *  `hmi/` 不动。 */
export const MOBILE_QUICK_COMMAND_ORDER: readonly string[] = [
  '今天天气怎么样',
  '附近的充电站',
  '讲个笑话',
  '打开空调26度',
  '播放音乐',
  '打开主驾座椅加热',
  '导航去首都机场',
  '我今天有点不开心',
]

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
