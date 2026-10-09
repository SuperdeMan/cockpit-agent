// LLM 用量按 caller × model 汇总；显示缺失口径，不把缺失数据补成零。
import { useEffect, useRef, useState } from 'react'
import { fetchLlmSummary } from '../api'
import { Banner, Duration, EmptyState, ErrorState, ShareBar, SkeletonRow, StatTile, Table, Tag } from '../components/data'
import { Button, Segmented } from '../components/ui'
import { errorMessage, fmtTime } from '../components/inspector/model'
import type { LlmSummary, Turn } from '../types'
import '../usage.css'

const WINDOWS = [
  { value: '1', label: '1 小时' },
  { value: '24', label: '24 小时' },
  { value: '168', label: '7 天' },
  { value: '720', label: '30 天' },
]
const number = (value: number) => Number.isFinite(value) ? value.toLocaleString('en-US') : '—'

export function fmtTokens(n: number): string {
  if (!Number.isFinite(n)) return '—'
  if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + '万'
  return number(n)
}

export function LlmView({ lastTurn }: { lastTurn: Turn | null }) {
  const [hours, setHours] = useState(24)
  const [summary, setSummary] = useState<LlmSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const generation = useRef(0)

  useEffect(() => {
    const current = ++generation.current
    setLoading(true)
    setError('')
    void fetchLlmSummary(hours).then(value => {
      if (generation.current !== current) return
      setSummary(value)
      setLoading(false)
    }).catch(reason => {
      if (generation.current !== current) return
      setError(errorMessage(reason))
      setLoading(false)
    })
    return () => { if (generation.current === current) generation.current++ }
  }, [hours, lastTurn, refresh])

  const current = summary
  const groups = current?.groups ?? []
  const sorted = [...groups].sort((a, b) => Number(b.caller === '(未归属)') - Number(a.caller === '(未归属)'))
  const totals = groups.reduce((acc, group) => ({
    calls: acc.calls + group.calls,
    input: acc.input + group.prompt_tokens,
    output: acc.output + group.completion_tokens,
    errors: acc.errors + group.errors,
    blind: acc.blind + (group.caller === '(未归属)' ? group.calls : 0),
  }), { calls: 0, input: 0, output: 0, errors: 0, blind: 0 })
  const totalTokens = totals.input + totals.output
  const tokenUnknown = totals.calls > 0 && totalTokens === 0
  const hasUsageCounts = groups.every(group => typeof group.zero_usage_calls === 'number' && Number.isFinite(group.zero_usage_calls))
  const hasFallbackCounts = groups.every(group => typeof group.fallback_calls === 'number' && Number.isFinite(group.fallback_calls))
  const zeroUsage = hasUsageCounts ? groups.reduce((sum, group) => sum + group.zero_usage_calls!, 0)
    : groups.filter(group => group.prompt_tokens === 0 && group.completion_tokens === 0).length
  const fallback = hasFallbackCounts ? groups.reduce((sum, group) => sum + group.fallback_calls!, 0) : null
  const callers = new Set(groups.map(group => group.caller)).size
  const displayedHours = current?.hours ?? hours
  const windowLabel = WINDOWS.find(window => Number(window.value) === displayedHours)?.label ?? displayedHours + ' 小时'

  return <main className="obs-usage" aria-label="LLM 用量" data-refreshing={loading && !!current || undefined}>
    <h1 className="sr-only">LLM 用量</h1>
    <div className="obs-usage__filters">
      <span className="caption">时间窗</span>
      <Segmented aria-label="LLM 用量时间窗" value={String(hours)} items={WINDOWS} onChange={value => setHours(Number(value))} />
      <span className="obs-usage__window-note" role="status">{current && (loading || error)
        ? (loading ? '正在更新' : '更新失败') + ' · 显示上次结果（' + windowLabel + '）' : '按调用方与模型汇总'}</span>
    </div>
    {error && !current ? <ErrorState title="用量加载失败" description={error} onRetry={() => setRefresh(value => value + 1)} />
      : !current ? <div aria-busy="true" className="obs-usage__loading">
        {Array.from({ length: 6 }, (_, index) => <SkeletonRow key={index} kind="table" />)}
      </div> : <>
        {error && <Banner tone="critical" action={<Button size="sm" onClick={() => setRefresh(value => value + 1)}>重试</Button>}>
          用量更新失败：{error}{' · 以下仍为上次 ' + windowLabel + '的结果。'}
        </Banner>}
        <div className="obs-usage__tiles" aria-label={'最近 ' + windowLabel + ' 用量汇总'} aria-busy={loading}>
          <StatTile label="调用次数" value={number(totals.calls)} unit="次" note={'近 ' + windowLabel + ' · ' + callers + ' 个调用方'} />
          <StatTile label="tokens" value={tokenUnknown ? '—' : number(totalTokens)}
            note={tokenUnknown ? '输入与输出均未上报' : '入 ' + number(totals.input) + ' · 出 ' + number(totals.output)} />
          <StatTile label="错误" value={number(totals.errors)} unit="次"
            note={'降级另计：' + (fallback === null ? '— 未上报' : number(fallback) + ' 次')} />
          <StatTile label="未归属调用" value={number(totals.blind)} unit="次" status={totals.blind ? 'critical' : 'normal'}
            note={totals.blind ? '归属盲区 · 应为 0（占 ' + Math.round(totals.blind / totals.calls * 100) + '%）' : '归属盲区 · 应为 0'} />
          <StatTile label={hasUsageCounts ? '未上报用量' : '未上报分组'} value={number(zeroUsage)} unit={hasUsageCounts ? '次' : '组'}
            status={zeroUsage ? 'warn' : 'normal'} note={hasUsageCounts ? 'tokens 均为 0 的成功调用 · 应为 0' : '旧接口 · 未提供成功调用数'} />
        </div>
        <div className="obs-usage__results" aria-busy={loading}>
          {!groups.length ? <EmptyState title="该时间窗内没有 LLM 调用" description="切换时间窗，或等待下一次模型调用。" />
            : <Table className="obs-usage__table" aria-label="LLM 用量归属">
              <colgroup><col className="obs-usage__caller-column" /><col className="obs-usage__model-column" /><col /><col /><col />
                <col className="obs-usage__share-column" /><col /><col /><col className="obs-usage__time-column" /></colgroup>
              <thead><tr><th scope="col">调用方</th><th scope="col">模型</th><th scope="col" className="obs-table__numeric">次数</th>
                <th scope="col" className="obs-table__numeric">输入</th><th scope="col" className="obs-table__numeric">输出</th><th scope="col">占比（按 tokens）</th>
                <th scope="col" className="obs-table__numeric">错误</th><th scope="col">平均时延</th><th scope="col">最近调用</th></tr></thead>
              <tbody>{sorted.map(group => {
                const unreported = group.prompt_tokens === 0 && group.completion_tokens === 0
                const blind = group.caller === '(未归属)'
                return <tr key={group.caller + '|' + group.model} className={blind ? 'obs-usage__blind' : ''}>
                  <td><span className="obs-usage__caller">{group.caller}{blind && <Tag tone="critical">归属盲区</Tag>}</span></td>
                  <td className="obs-usage__model">{group.model}</td>
                  <td className="obs-table__numeric">{number(group.calls)}</td>
                  <td className={'obs-table__numeric' + (unreported ? ' obs-text--warn' : '')}>{unreported ? '— 未上报' : number(group.prompt_tokens)}</td>
                  <td className={'obs-table__numeric' + (unreported ? ' obs-text--warn' : '')}>{unreported ? '— 未上报' : number(group.completion_tokens)}</td>
                  <td><ShareBar value={unreported || totalTokens === 0 ? null : (group.prompt_tokens + group.completion_tokens) / totalTokens * 100}
                    label={group.caller + ' ' + group.model + ' tokens 占比'} /></td>
                  <td className={'obs-table__numeric' + (group.errors ? ' obs-text--critical' : '')}>{number(group.errors)}</td>
                  <td><Duration ms={group.avg_latency_ms} /></td>
                  <td className="obs-usage__timestamp">{fmtTime(group.last_ts)}</td>
                </tr>
              })}</tbody>
            </Table>}
        </div>
        <p className="obs-usage__footnote">未归属调用置顶 · 输入与输出均为 0 时显示“— 未上报” · 错误与降级分别计数
          {!hasUsageCounts && ' · 未上报分组数不代表调用次数，也无法判断其中成功调用数。'}</p>
      </>}
  </main>
}
