import { useState } from 'react'
import { Button, Checkbox, FilterChip, Input, Segmented, Slider, TabGroup } from './components/ui'
import { Banner, CodeBlock, Duration, EmptyState, ErrorState, IdChip, KVRow, OutcomeBadge, ShareBar, SkeletonRow, StatTile, Tag } from './components/data'
import { OUTCOME_DISPLAY } from './outcomeDisplay'

export function FixtureGallery() {
  const [segment, setSegment] = useState('dark')
  const [value, setValue] = useState(50)
  const [checked, setChecked] = useState(true)
  return <main className="fixture-gallery" aria-label="基础组件离线夹具">
    <section className="panel stack"><h1>基础件 · 示例数据</h1><p className="caption">Figma 02 Foundations / 03 Components</p>
      <div className="row wrap"><Button kind="primary">主操作</Button><Button>次操作</Button><Button kind="ghost">文字操作</Button><Button kind="danger">危险操作</Button><Button disabled>不可用</Button></div>
      <Input aria-label="示例输入" placeholder="搜索原话 / 粘贴 trace" />
      <Segmented value={segment} onChange={setSegment} items={[{ value: 'dark', label: '深' }, { value: 'light', label: '浅' }, { value: 'system', label: '跟随系统' }]} />
      <Checkbox checked={checked} onChange={e => setChecked(e.target.checked)} label="选项" />
      <div className="row wrap"><FilterChip selected>已选筛选</FilterChip> <FilterChip>筛选</FilterChip><Tag>来源标签</Tag></div>
      <Slider label="模拟滑块" value={value} min={0} max={100} onChange={setValue} />
      <TabGroup value={segment} onChange={setSegment} items={[{ value: 'dark', label: '时间线' }, { value: 'light', label: '日志', count: 0 }]} aria-label="示例页签" />
    </section>
    <section className="panel stack"><h2>结局徽标 · 全词表</h2><div className="row wrap">{Object.keys(OUTCOME_DISPLAY).map(outcome => <OutcomeBadge key={outcome} turn={{ outcome, status: 'ok' }} />)}</div><div className="row wrap">{['ok', 'need_confirm', 'err', 'timeout'].map(status => <OutcomeBadge key={status} turn={{ status }} />)}</div></section>
    <section className="panel stack"><h2>数据与状态</h2><IdChip value="fixture-0000000000000000001" /><Duration ms={6400} /><KVRow label="规划通道" value="toolcall" /><CodeBlock code={'{\n  "source": "offline fixture"\n}'} /><ShareBar value={62} /><StatTile label="未归属调用" value={4} unit="次" note="示例：归属盲区" status="critical" /><Banner tone="warn">实时通道重连中 · 数据可能滞后</Banner><SkeletonRow /></section>
    <section className="panel stack"><EmptyState /><ErrorState description="示例错误，不会访问服务器" code="HTTP 503" onRetry={() => {}} /></section>
  </main>
}
