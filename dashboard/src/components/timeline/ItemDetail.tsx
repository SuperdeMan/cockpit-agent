import { useState } from 'react'
import { laneLabel } from '../../laneOf'
import { CodeBlock, Duration, KVRow, Tag } from '../data'
import { capturePlaceholder } from '../inspector/model'
import { Button, IconButton } from '../ui'
import { formatRelative, isPending, logTone, type TimelineItem } from './model'

function text(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

function AttributeValue({ value }: { value: unknown }) {
  const display = text(value)
  if (display.length <= 100 && !display.includes('\n')) return <span>{display}</span>
  return <details className="timeline-long-value"><summary>{display.replace(/\s+/g, ' ').slice(0, 72)}…</summary>
    <CodeBlock code={display} lang={typeof value === 'string' ? 'TEXT' : 'JSON'} />
  </details>
}

export function ItemDetail({ item, items, onClose, onSelect }: {
  item: TimelineItem; items: readonly TimelineItem[]; onClose: () => void; onSelect: (item: TimelineItem) => void
}) {
  const [copyState, setCopyState] = useState('')
  const statusTone = item.kind === 'log' ? logTone(item.raw.level) : item.status === 'err' ? 'critical'
    : isPending(item.status) ? 'pending' : 'neutral'
  const overlap = item.start === null || item.end === null || !(item.duration && item.duration > 0) ? []
    : items.filter(other => other.id !== item.id && other.kind !== 'log' && other.start !== null && other.end !== null
      && other.end > item.start! && other.start < item.end!
      && (other.kind === 'llm' || other.lane === 'external'))
  const copyAttrs = async () => {
    if (item.kind !== 'span') return
    try { await navigator.clipboard.writeText(JSON.stringify(item.raw.attrs ?? {}, null, 2)); setCopyState('已复制') }
    catch { setCopyState('复制失败') }
  }
  return <aside className="timeline-item-detail" aria-label="选中记录详情">
    <div className="timeline-detail-heading"><span>选中</span><IconButton icon="close" label="关闭记录详情" size="sm" onClick={onClose} /></div>
    <div className="timeline-detail-title"><h3>{item.label}</h3><Tag tone={statusTone}>{item.status || '—'}</Tag></div>
    <KVRow label="泳道" value={laneLabel(item.lane)} />
    <KVRow label="时长" value={item.kind === 'log' ? '事件' : <Duration ms={item.duration} />} mono />
    <KVRow label="相对起止" value={`${formatRelative(item.start)} → ${formatRelative(item.end)}`} mono />
    {item.kind === 'span' && <>
      <KVRow label="服务" value={item.raw.service || '— 未上报'} mono />
      <KVRow label="span_id" value={item.raw.span_id || '— 未上报'} mono />
      <KVRow label="父 span" value={item.raw.parent_id || '— 采集未带'} mono />
      {item.marker === 'shadow' && <Tag>影子判定 · 不影响执行</Tag>}
      <div className="timeline-detail-section"><h4>attrs</h4>
        {Object.entries(item.raw.attrs ?? {}).map(([key, value]) => <KVRow key={key} label={key} value={<AttributeValue value={value} />} mono />)}
        {!Object.keys(item.raw.attrs ?? {}).length && <p className="caption">没有附加字段</p>}
        <div className="row"><Button size="sm" kind="secondary" icon="copy" onClick={copyAttrs}>复制 attrs</Button><span role="status" className="caption">{copyState}</span></div>
      </div>
    </>}
    {item.kind === 'llm' && <>
      <KVRow label="调用方" value={item.raw.caller || '(未归属)'} mono />
      <KVRow label="模型" value={item.raw.model || '— 未上报'} mono />
      <KVRow label="厂商" value={item.raw.provider || '— 未上报'} mono />
      <KVRow label="降级换厂商" value={item.raw.fallback === undefined ? '— 未上报' : item.raw.fallback ? '是' : '否'} />
      <KVRow label="请求档位" value={item.raw.requested_tier || '— 未上报'} mono />
      <KVRow label="锁定" value={item.raw.pinned === undefined ? '— 未上报' : item.raw.pinned ? '是' : '否'} />
      <KVRow label="输入 / 输出" value={item.raw.prompt_tokens === 0 && item.raw.completion_tokens === 0
        ? '— / — · usage 未上报' : `${item.raw.prompt_tokens ?? '—'} / ${item.raw.completion_tokens ?? '—'} tok`} mono />
      <KVRow label="缓存命中" value={item.raw.cache_hit ? '是' : '否'} />
      <KVRow label="思考" value={item.raw.thinking ? '是' : '否'} />
      {item.raw.error && <CodeBlock code={item.raw.error} lang="ERROR" />}
      <details className="timeline-detail-section"><summary>提示词末段 / 输出头部</summary>
        {capturePlaceholder(item.raw.prompt_tail)
          ? <KVRow label="提示词末段" value={capturePlaceholder(item.raw.prompt_tail)} />
          : <CodeBlock code={item.raw.prompt_tail || '— 未采集'} lang="PROMPT TAIL" />}
        {capturePlaceholder(item.raw.content_head)
          ? <KVRow label="输出头部" value={capturePlaceholder(item.raw.content_head)} />
          : <CodeBlock code={item.raw.content_head || '— 未采集'} lang="CONTENT HEAD" />}
      </details>
    </>}
    {item.kind === 'log' && <>
      <KVRow label="服务" value={item.raw.service || '—'} mono />
      <KVRow label="logger" value={item.raw.logger || '—'} mono />
      <CodeBlock code={item.raw.msg || '—'} lang={item.raw.level || 'LOG'} />
    </>}
    {!!overlap.length && <section className="timeline-detail-section">
      <h4>同一时段的调用</h4><p className="caption">按时间重叠推断，不是父子关系。</p>
      {overlap.map(other => <button type="button" key={other.id} className="timeline-overlap" onClick={() => onSelect(other)}>
        <span>{other.label}</span><Duration ms={other.duration} />
      </button>)}
    </section>}
  </aside>
}
