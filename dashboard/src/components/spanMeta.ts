// span 节点 → 视觉分层（TracePanel 平铺与 SpanWaterfall 瀑布共用同一套色系语义）
export function nodeClass(node: string): string {
  if (
    node.startsWith('route.local') ||
    node.startsWith('route.multi') ||
    node.startsWith('step.edge')
  )
    return 'trace-node--edge'
  if (node.startsWith('val')) return 'trace-node--val'
  if (node.startsWith('cloud.planning')) return 'trace-node--llm'
  if (node.startsWith('step.tool')) return 'trace-node--tool'
  if (node.startsWith('suspend') || node.startsWith('route.mixed'))
    return 'trace-node--wait'
  if (
    node.startsWith('route.cloud') ||
    node.startsWith('step.agent') ||
    node.startsWith('aggregate') ||
    node.startsWith('t2')
  )
    return 'trace-node--cloud'
  return 'trace-node--default'
}

export const LEGEND: ReadonlyArray<readonly [string, string]> = [
  ['端侧', 'var(--n-edge)'],
  ['云端', 'var(--n-cloud)'],
  ['VAL', 'var(--n-val)'],
  ['LLM', 'var(--n-llm)'],
  ['工具', 'var(--n-tool)'],
  ['挂起', 'var(--n-wait)'],
]

export const NODE_COLOR: Record<string, string> = {
  'trace-node--edge': 'var(--n-edge)',
  'trace-node--val': 'var(--n-val)',
  'trace-node--llm': 'var(--n-llm)',
  'trace-node--tool': 'var(--n-tool)',
  'trace-node--wait': 'var(--n-wait)',
  'trace-node--cloud': 'var(--n-cloud)',
  'trace-node--default': 'var(--ink-3)',
}
