import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { mockApi, renderApp } from '../../test/utils'

describe('Terms page', () => {
  it('credits the ward boundary data (Q28)', async () => {
    mockApi()
    renderApp('/terms')
    expect(await screen.findByRole('heading', { name: 'Map data' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Ward boundaries: Greater Bengaluru Authority, via OpenCity (data.opencity.in)',
      ),
    ).toBeInTheDocument()
  })
})
