// 首页系统推荐的可用性筛选（AR05 §6.2 / R14）。
//
// 问题：默认推荐里前两条是车控。当前账号没有车控授权时，它们仍然摆在首页——
// 点下去必然被婉拒，而用户看不出为什么。推荐是一种**承诺**，承诺了做不到比不承诺更糟。
//
// 三条纪律：
// 1. **只筛系统示例**。用户自定义短语不在绑定表里，一律原样保留——那是他自己的输入，
//    系统无权替他判「你这句现在用不了」，更不能把它从设置里删掉。
// 2. **摘要不完整就不筛**。`summaryStatus='partial'`（云端只取到一半）或压根没摘要时
//    照常全列：宁可显示一条点了会被婉拒的推荐，也不能因为「此刻查不到」把能力藏起来
//    ——后者会让用户以为自己没有这个功能。
// 3. **未出现在摘要里的能力按不可用处理**（完整摘要下）。完整摘要说了它有哪些能力，
//    没说的就是没有；这时候还摆出来就是替服务端做了没有依据的承诺。
//
// 声明（示例 → 能力、UI id → 服务端 agent_id）住在 `types.ts` 的 AGENT_CATALOG /
// SYSTEM_QUICK_COMMAND_AGENTS，**由调用方注入**：这里只放判据，不抄第二份表。

/**
 * @param {string[]} commands 用户当前的快捷指令列表（含系统默认与自定义）
 * @param {{summaryStatus?: string, capabilities?: Array<{id: string, status: string}>} | null} summary
 * @param {Record<string, boolean> | undefined} enabledAgents 用户的能力开关（false=关）
 * @param {Record<string, string>} bindings 系统示例 → 能力目录 id
 * @param {(catalogId: string) => string} [serverIdOf] 能力目录 id → 服务端 agent_id（缺省同名）
 */
export function visibleQuickCommands(commands, summary, enabledAgents, bindings, serverIdOf) {
  const list = Array.isArray(commands) ? commands.filter((c) => typeof c === 'string' && c) : []
  const table = bindings || {}
  const switches = enabledAgents || {}
  const toServerId = typeof serverIdOf === 'function' ? serverIdOf : (id) => id
  const complete = !!summary && summary.summaryStatus !== 'partial'
  const available = new Set(
    complete
      ? (summary.capabilities || [])
        .filter((c) => c && c.status === 'available' && c.id)
        .map((c) => c.id)
      : [],
  )
  return list.filter((cmd) => {
    const agentId = table[cmd]
    if (!agentId) return true                       // 用户自定义：原样保留
    if (switches[agentId] === false) return false   // 用户自己关掉的能力不再推荐
    if (!complete) return true                      // 摘要不完整：不筛
    return available.has(toServerId(agentId))
  })
}
