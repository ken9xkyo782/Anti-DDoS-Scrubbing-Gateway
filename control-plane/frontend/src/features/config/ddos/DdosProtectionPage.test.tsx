import { render, screen, cleanup } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, afterEach } from 'vitest'
import { DdosProtectionPage } from './DdosProtectionPage'

describe('DdosProtectionPage', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders the read-only protection coverage overview', () => {
    render(
      <MemoryRouter>
        <DdosProtectionPage />
      </MemoryRouter>
    )

    expect(screen.getByText('DDoS Protection')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Protection coverage' })).toBeInTheDocument()
    // Exact name — the cross-link card below adds a "Tune UDP reflection &
    // amplification" heading that a loose regex would also match.
    expect(
      screen.getByRole('heading', { name: 'UDP reflection & amplification' })
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Spoofed & bogon sources/i })).toBeInTheDocument()
  })

  it('delegates blocked-port management to the amplification tab', () => {
    render(
      <MemoryRouter>
        <DdosProtectionPage />
      </MemoryRouter>
    )

    // Port management moved out — no table, no add/remove affordances here.
    expect(screen.queryByText('Dynamic blocked source ports')).not.toBeInTheDocument()
    expect(screen.queryByText('UDP/53')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add Blocked Port/i })).not.toBeInTheDocument()

    const link = screen.getByRole('link', { name: /Manage blocked source ports/i })
    expect(link).toHaveAttribute('href', '/admin/amplification')
  })
})
