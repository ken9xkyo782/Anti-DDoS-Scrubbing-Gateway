import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { TopTalkersPanel } from './TopTalkersPanel'

describe('TopTalkersPanel', () => {
  afterEach(cleanup)

  it('lists sampled top ports and sources with an approximate label', () => {
    render(
      <TopTalkersPanel
        topDstPorts={[
          { port: 443, count: 40 },
          { port: 53, count: 12 },
        ]}
        topSrc={[{ ip: '198.51.100.10', count: 25 }]}
      />,
    )

    expect(screen.getByText(/approximate/i)).toBeInTheDocument()
    expect(screen.getByText('Port 443: 40')).toBeInTheDocument()
    expect(screen.getByText('198.51.100.10: 25')).toBeInTheDocument()
  })

  it('shows empty states when no samples were collected', () => {
    render(<TopTalkersPanel topDstPorts={[]} topSrc={[]} />)

    expect(screen.getByText('No sampled destination ports in this window.')).toBeInTheDocument()
    expect(screen.getByText('No sampled source addresses in this window.')).toBeInTheDocument()
  })
})
