import { render, screen } from '@testing-library/react'

import { VehicleState } from './VehicleState'

test('aggregates hvac on/temp/wind into one card and highlights on member change', () => {
  render(
    <VehicleState
      state={{ hvac_on: true, hvac_temp: 26, hvac_wind_speed: 3 }}
      changed={new Set(['hvac_temp'])}
    />,
  )

  const card = document.querySelector('[data-key="hvac"]')
  expect(card).toBeTruthy()
  expect(card?.textContent).toContain('26')
  expect(card?.textContent).toContain('3') // 风速
  expect(card?.className).toContain('changed') // 成员变化 → 整卡高亮
  // 成员键不再单独成卡
  expect(document.querySelector('[data-key="hvac_temp"]')).toBeNull()
})

test('renders ambient color as the real color (protocol value → swatch + 中文)', () => {
  render(
    <VehicleState
      state={{ ambient_light: true, ambient_light_color: 'blue', ambient_light_brightness: 80 }}
      changed={new Set()}
    />,
  )

  const card = document.querySelector('[data-key="ambient"]')
  expect(card?.textContent).toContain('蓝色') // 不再显示英文 blue
  expect(card?.textContent).toContain('80') // 亮度
  const swatch = card?.querySelector('.swatch')
  // 命中真实蓝色 #60a5fa = rgb(96, 165, 250)，而不是灰色兜底 #9db0d4
  expect(swatch?.getAttribute('style') ?? '').toContain('rgb(96, 165, 250)')
})

test('renders window open-degree as a percentage bar', () => {
  render(<VehicleState state={{ window: '70%' }} changed={new Set()} />)

  const card = document.querySelector('[data-key="window"]')
  expect(card?.textContent).toContain('70%')
  const fill = card?.querySelector('.vbar i') as HTMLElement
  expect(fill.style.width).toBe('70%')
})

test('maps closed window to 0% and open to 100%', () => {
  const { rerender } = render(<VehicleState state={{ window: 'closed' }} changed={new Set()} />)
  expect((document.querySelector('[data-key="window"] .vbar i') as HTMLElement).style.width).toBe('0%')

  rerender(<VehicleState state={{ window: 'open' }} changed={new Set()} />)
  expect((document.querySelector('[data-key="window"] .vbar i') as HTMLElement).style.width).toBe('100%')
})

test('aggregates media playback state and volume into one card', () => {
  render(<VehicleState state={{ media: 'playing', volume: 30 }} changed={new Set()} />)

  const card = document.querySelector('[data-key="media"]')
  expect(card?.textContent).toContain('播放中')
  expect(card?.textContent).toContain('30')
  expect(document.querySelector('[data-key="volume"]')).toBeNull()
})

test('organizes states under section headers and hides empty groups', () => {
  render(<VehicleState state={{ window: 'closed', headlight: true }} changed={new Set()} />)

  expect(screen.getByText('门窗车身')).toBeTruthy()
  expect(screen.getByText('灯光')).toBeTruthy()
  // 没有空调/影音/驾驶状态时，这些分组不渲染
  expect(screen.queryByText('影音')).toBeNull()
  expect(screen.queryByText('驾驶')).toBeNull()
})

test('translates driving mode codes to 中文', () => {
  render(<VehicleState state={{ driving_mode: 'sport' }} changed={new Set()} />)

  const card = document.querySelector('[data-key="driving_mode"]')
  expect(card?.textContent).toContain('运动')
})
