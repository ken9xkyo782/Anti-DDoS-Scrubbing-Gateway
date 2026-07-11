import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider, useAuth } from './AuthContext'

const principal = {
  id: 'a4f3df34-a15b-4482-9e16-5b5604c7ae9d',
  username: 'admin',
  role: 'admin',
  tenant_id: null,
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function LoginProbe() {
  const { isLoading, login, principal: currentPrincipal } = useAuth()

  return (
    <>
      <p>{isLoading ? 'Loading' : currentPrincipal?.username ?? 'Signed out'}</p>
      <button type="button" onClick={() => void login({ username: 'admin', password: 'secret' })}>
        Sign in
      </button>
    </>
  )
}

function renderWithAuth(children: ReactNode) {
  return render(<AuthProvider>{children}</AuthProvider>)
}

describe('AuthProvider', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    window.history.replaceState(null, '', '/login')
  })

  it('logs in and refreshes the role from the session endpoint', async () => {
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(jsonResponse(principal))
      .mockResolvedValueOnce(jsonResponse(principal))

    renderWithAuth(<LoginProbe />)

    await screen.findByText('Signed out')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByText('admin')
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/auth/login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ username: 'admin', password: 'secret' }),
      }),
    )
    await waitFor(() => expect(fetchMock).toHaveBeenNthCalledWith(3, '/auth/me', expect.any(Object)))
  })
})
