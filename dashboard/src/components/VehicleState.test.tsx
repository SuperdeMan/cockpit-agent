import { render, screen } from '@testing-library/react'

import { VehicleState } from './VehicleState'

test('renders state values and highlights changed keys', () => {
  render(
    <VehicleState
      state={{ hvac_temp: 26, window: 'closed' }}
      changed={new Set(['hvac_temp'])}
    />,
  )

  expect(screen.getByText('26')).toBeTruthy()
  const card = document.querySelector('[data-key="hvac_temp"]')
  expect(card?.className).toContain('changed')
})
