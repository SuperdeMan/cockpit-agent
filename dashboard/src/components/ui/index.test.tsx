import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Button, Dialog, FilterChip, Popover, Segmented, TabGroup, Tooltip } from '.'

afterEach(cleanup)

test('dialog traps both Tab directions, dismisses with Escape and restores its opener', () => {
  function Example() {
    const [open, setOpen] = useState(false)
    return <><Button onClick={() => setOpen(true)}>打开</Button><Dialog open={open} onClose={() => setOpen(false)} title="重放确认"
      footer={<><Button onClick={() => setOpen(false)}>取消</Button><Button>确认重放</Button></>}>
      <p>这会重新向网关发出原话。</p>
    </Dialog></>
  }
  render(<Example />)
  const opener = screen.getByRole('button', { name: '打开' })
  opener.focus()
  fireEvent.click(opener)
  const first = screen.getByRole('button', { name: '关闭对话框' })
  const last = screen.getByRole('button', { name: '确认重放' })
  expect(document.activeElement).toBe(first)
  fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(last)
  fireEvent.keyDown(last, { key: 'Tab' })
  expect(document.activeElement).toBe(first)
  opener.focus()
  expect(document.activeElement).toBe(first)
  fireEvent.keyDown(first, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

test('popover restores the opener on Escape and closing an internal action', () => {
  function Example() {
    const [open, setOpen] = useState(false)
    return <Popover open={open} onClose={() => setOpen(false)} title="筛选"
      anchor={<Button onClick={() => setOpen(true)}>打开筛选</Button>}>
      <Button onClick={() => setOpen(false)}>完成筛选</Button>
    </Popover>
  }
  render(<Example />)
  const opener = screen.getByRole('button', { name: '打开筛选' })
  opener.focus()
  fireEvent.click(opener)
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '完成筛选' }))
  fireEvent.click(screen.getByRole('button', { name: '完成筛选' }))
  expect(document.activeElement).toBe(opener)
  fireEvent.click(opener)
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(opener)
})

test('popover escapes clipping parents, clamps to viewport and treats portal clicks as inside', () => {
  const rect = (x: number, y: number, width: number, height: number) => ({
    x, y, width, height, top: y, right: x + width, bottom: y + height, left: x, toJSON: () => ({}),
  })
  const original = HTMLElement.prototype.getBoundingClientRect
  const measure = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('obs-popover-anchor')) return rect(10, window.innerHeight - 50, 70, 32)
    if (this.classList.contains('obs-popover')) return rect(0, 0, 320, 240)
    return original.call(this)
  })
  try {
    function Example() {
      const [open, setOpen] = useState(false)
      return <div data-testid="clipped" style={{ overflow: 'hidden', width: 100 }}><Popover open={open} onClose={() => setOpen(false)} title="浮层"
        anchor={<Button onClick={() => setOpen(true)}>触发</Button>}><Button>面板内操作</Button></Popover></div>
    }
    render(<Example />)
    const opener = screen.getByRole('button', { name: '触发' })
    opener.focus(); fireEvent.click(opener)
    const panel = screen.getByRole('dialog', { name: '浮层' })
    expect(screen.getByTestId('clipped').contains(panel)).toBe(false)
    expect(panel.style.left).toBe('8px')
    expect(Number.parseFloat(panel.style.top)).toBeLessThan(window.innerHeight - 50)
    fireEvent.pointerDown(screen.getByRole('button', { name: '面板内操作' }))
    expect(screen.getByRole('dialog', { name: '浮层' })).toBeTruthy()
    fireEvent.keyDown(panel, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)
  } finally { measure.mockRestore() }
})

test('tooltip uses a clamped portal, follows focus and dismisses hover content with Escape', () => {
  const rect = (x: number, y: number, width: number, height: number) => ({
    x, y, width, height, top: y, right: x + width, bottom: y + height, left: x, toJSON: () => ({}),
  })
  const original = HTMLElement.prototype.getBoundingClientRect
  const measure = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('obs-tooltip-anchor')) return rect(window.innerWidth - 20, 2, 24, 24)
    if (this.classList.contains('obs-tooltip')) return rect(0, 0, 320, 80)
    return original.call(this)
  })
  try {
    render(<div data-testid="timeline-clip" style={{ overflow: 'hidden' }}><Tooltip content="日志详情"><Button aria-describedby="existing">日志标记</Button></Tooltip></div>)
    const button = screen.getByRole('button', { name: '日志标记' })
    fireEvent.focus(button)
    const tooltip = screen.getByRole('tooltip')
    expect(screen.getByTestId('timeline-clip').contains(tooltip)).toBe(false)
    expect(Number.parseFloat(tooltip.style.left) + 320).toBe(window.innerWidth - 8)
    expect(Number.parseFloat(tooltip.style.top)).toBeGreaterThan(26)
    expect(button.getAttribute('aria-describedby')).toBe(`existing ${tooltip.id}`)
    fireEvent.mouseLeave(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(button.getAttribute('aria-describedby')).toBe('existing')
    fireEvent.blur(button)
    fireEvent.mouseEnter(button)
    expect(screen.getByRole('tooltip')).toBeTruthy()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
  } finally { measure.mockRestore() }
})

test.each(['tabs', 'segments'])('%s use roving focus and skip disabled choices', kind => {
  const onChange = vi.fn()
  const Choice = kind === 'tabs' ? TabGroup : Segmented
  render(<Choice value="a" onChange={onChange} aria-label="选择视图" items={[
    { value: 'a', label: '时间线' }, { value: 'b', label: '不可选', disabled: true }, { value: 'c', label: '原始' },
  ]} />)
  const first = screen.getByText('时间线')
  const last = screen.getByText('原始')
  first.focus()
  fireEvent.keyDown(first, { key: 'ArrowRight' })
  expect(document.activeElement).toBe(last)
  expect(onChange).toHaveBeenLastCalledWith('c')
  fireEvent.keyDown(last, { key: 'Home' })
  expect(document.activeElement).toBe(first)
  expect(onChange).toHaveBeenLastCalledWith('a')
  expect(first.tabIndex).toBe(0)
  expect(last.tabIndex).toBe(-1)
})

test('removable chips have separate buttons and arrows move without toggling a filter', () => {
  const toggle = vi.fn(), remove = vi.fn()
  render(<div data-filter-chip-group><FilterChip onClick={toggle} onRemove={remove} selected>收藏</FilterChip>
    <FilterChip>告警</FilterChip></div>)
  const first = screen.getByRole('button', { name: '收藏' })
  first.focus()
  fireEvent.keyDown(first, { key: 'ArrowRight' })
  expect(document.activeElement).toBe(screen.getByRole('button', { name: '告警' }))
  expect(toggle).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '移除筛选' }))
  expect(remove).toHaveBeenCalledOnce()
  expect(toggle).not.toHaveBeenCalled()
  expect(document.querySelector('button button')).toBeNull()
})
