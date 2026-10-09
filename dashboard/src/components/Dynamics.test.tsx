import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { setVehicleEnv } from '../api'
import { Dynamics } from './Dynamics'
vi.mock('../api', () => ({ setVehicleEnv: vi.fn(async () => {}) }))
beforeEach(() => { vi.mocked(setVehicleEnv).mockReset().mockResolvedValue() })
afterEach(() => cleanup())
test('shows observed environment without recreating the VAL speed decision', () => {
  const { container } = render(<Dynamics state={{ speed_kmh: 160, battery: 72, gear: 'D' }} />)
  expect(screen.getByText(/车速 160 km\/h/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /模拟环境/ }))
  expect(container.querySelector('.armed')).toBeNull()
  expect(screen.queryByText(/120/)).toBeNull()
  expect(setVehicleEnv).not.toHaveBeenCalled()
})
test('missing signals are unknown rather than zero or parked', () => {
  render(<Dynamics state={{}} />)
  expect(screen.getByText(/车速 — km\/h/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /模拟环境/ }))
  expect((screen.getByRole('slider', { name: '车速' }) as HTMLInputElement).disabled).toBe(true)
  expect(document.querySelector('[aria-checked="true"]')).toBeNull()
})
test('a server debug-disabled reply disables every environment control', async () => {
  vi.mocked(setVehicleEnv).mockRejectedValue(new Error('debug disabled'))
  render(<Dynamics state={{ speed_kmh: 10, battery: 80, gear: 'P' }} />)
  fireEvent.click(screen.getByRole('button', { name: /模拟环境/ }))
  fireEvent.click(screen.getByRole('radio', { name: 'R' }))
  await screen.findByText(/调试写入已关闭/)
  await waitFor(() => expect((screen.getByRole('radio', { name: 'D' }) as HTMLButtonElement).disabled).toBe(true))
  expect(setVehicleEnv).toHaveBeenCalledWith('gear', 'R')
})
