import { render, screen } from '@testing-library/react'

import { Dynamics } from './Dynamics'

test('renders speed and battery from state', () => {
  render(<Dynamics state={{ speed_kmh: 60, battery: 72 }} />)

  expect(screen.getByText(/车速 60 km\/h/)).toBeTruthy()
  expect(screen.getByText(/电量 72%/)).toBeTruthy()
})

test('missing signals are unknown rather than zero speed or parked', () => {
  render(<Dynamics state={{}} />)
  expect(screen.getByText(/车速 — km\/h/)).toBeTruthy()
  expect(screen.getByText(/电量 —%/)).toBeTruthy()
  expect(document.querySelector('.gear.on')).toBeNull()
})
