import { render, screen, cleanup } from '@testing-library/react'
import { describe, expect, it, afterEach } from 'vitest'
import { DdosCoveragePage } from './DdosCoveragePage'

describe('DdosCoveragePage (tenant read-only view)', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders the protection coverage summary', () => {
    render(<DdosCoveragePage />)

    expect(screen.getByText('DDoS Protection')).toBeInTheDocument()
    expect(screen.getByText('Protection coverage')).toBeInTheDocument()
    expect(screen.getByText('Volumetric floods & scans')).toBeInTheDocument()
    expect(screen.getByText('UDP reflection & amplification')).toBeInTheDocument()
    expect(screen.getByText('Capacity & fairness guards')).toBeInTheDocument()
  })

  it('hides all blocked-port management surfaces from tenant users', () => {
    render(<DdosCoveragePage />)

    // No admin-only port administration is exposed
    expect(
      screen.queryByText('Built-in blocked source ports (always on)')
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Dynamic blocked source ports')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add Blocked Port/i })).not.toBeInTheDocument()
  })
})
