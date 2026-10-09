import { useEffect, useRef, useState } from 'react'
import { COLLECTOR_URL, changeOperatorToken } from '../../api'
import { navigate, shareableUrl, type ViewKey } from '../../navigation'
import { initialSize, initialTheme, applyPreferences, savePreference, type Size, type Theme } from '../../preferences'
import { Button, IconButton, Input, Menu, Popover, Segmented, TabGroup } from '../ui'
import { Tag } from '../data'

export function StackBadge() {
  const url = new URL(COLLECTOR_URL, location.origin)
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  const host = local ? url.host : url.hostname.endsWith('.ts.net') ? 'tailnet' : url.host
  return <Tag tone="outline" title={url.origin}>{local ? '本地' : '云端'} · {host}</Tag>
}

export function TopBar({ view, connected, nats, onList }: { view: ViewKey; connected: boolean; nats?: boolean; onList: () => void }) {
  const [theme, setTheme] = useState<Theme>(initialTheme)
  const [size, setSize] = useState<Size>(initialSize)
  const [menu, setMenu] = useState<'theme' | 'more' | null>(null)
  const [query, setQuery] = useState('')
  const [copy, setCopy] = useState('')
  const search = useRef<HTMLInputElement>(null)
  useEffect(() => {
    applyPreferences(theme, size)
    savePreference('theme', theme); savePreference('size', size)
    const media = matchMedia('(prefers-color-scheme: light)')
    const changed = () => applyPreferences(theme, size)
    media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [theme, size])
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); search.current?.focus() } }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])
  const copyLink = async () => {
    try { if (!navigator.clipboard) throw new Error(); await navigator.clipboard.writeText(shareableUrl()); setCopy('链接已复制') }
    catch { setCopy('复制失败，请复制地址栏链接') }
  }
  const chooseTheme = (value: string) => {
    setTheme(value as Theme)
    const url = new URL(location.href); url.searchParams.set('theme', value); history.replaceState(null, '', url)
  }
  const toggleSize = () => {
    const next = size === 'presentation' ? 'workbench' : 'presentation'
    setSize(next)
    const url = new URL(location.href); url.searchParams.set('size', next); history.replaceState(null, '', url)
  }
  return <header className="topbar">
    {(view === 'turns' || view === 'badcases') && <IconButton className="list-toggle" icon="sidebar" label="打开轮次列表" onClick={onList} />}
    <div className="topbar__brand"><img src="/brand.svg" width="24" height="24" alt="" /><h1>可观测台</h1></div>
    <TabGroup className="topbar__nav" aria-label="视图切换" value={view} onChange={value => navigate({ view: value as ViewKey, trace: '', query: '' })} items={[
      { value: 'turns', label: '轮次' }, { value: 'live', label: '实况' }, { value: 'logs', label: '日志' }, { value: 'llm', label: 'LLM 用量' }, { value: 'badcases', label: '收藏' },
    ]} />
    <form className="topbar__jump" onSubmit={e => { e.preventDefault(); navigate({ view: 'turns', trace: '', query: query.trim().replace(/^#/, '') }) }}>
      <Input ref={search} icon="search" shortcut="Ctrl K" aria-label="跳到 trace 或搜原话" placeholder="跳到 trace，或搜原话" value={query} onChange={e => setQuery(e.target.value)} />
    </form>
    <div className="topbar__stack"><StackBadge /></div>
    <span className={'connection ' + (connected ? 'connection--online' : '')} title={`NATS ${nats === undefined ? '状态未知' : nats ? '已连接' : '未连接'}；观测通道尽力而为，事件可能丢失`}><i aria-hidden="true" />Collector {connected ? '已连接' : '重连中'}</span>
    <Popover open={menu === 'theme'} onClose={() => setMenu(null)} title="外观" anchor={<IconButton icon="theme" label="选择主题" onClick={() => setMenu(menu === 'theme' ? null : 'theme')} />}><Segmented aria-label="主题" value={theme} onChange={chooseTheme} items={[{ value: 'dark', label: '深' }, { value: 'light', label: '浅' }, { value: 'system', label: '跟随系统' }]} /></Popover>
    <Button size="sm" kind="ghost" icon="monitor" aria-pressed={size === 'presentation'} onClick={toggleSize}>演示</Button>
    <Popover open={menu === 'more'} onClose={() => setMenu(null)} title="更多操作" anchor={<IconButton icon="more-vertical" label="更多操作" onClick={() => setMenu(menu === 'more' ? null : 'more')} />}><Menu items={[
      { id: 'token', label: '更换令牌', icon: 'key', onSelect: () => { setMenu(null); changeOperatorToken() } },
      { id: 'copy', label: '复制当前链接', icon: 'link', onSelect: copyLink },
    ]} /><p className="caption" role="status">{copy}</p></Popover>
  </header>
}
