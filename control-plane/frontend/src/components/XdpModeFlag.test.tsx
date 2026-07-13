import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { XdpModeFlag } from './XdpModeFlag'

describe('XdpModeFlag', () => {
  afterEach(cleanup)

  it.each(['generic', 'offline'] as const)('treats %s XDP mode as critical', (mode) => {
    render(<XdpModeFlag mode={mode} />)

    expect(screen.getByRole('alert')).toHaveTextContent(`Critical: XDP mode ${mode}`)
  })
})
