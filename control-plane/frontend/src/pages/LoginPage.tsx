import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

export function LoginPage() {
  const { isLoading, login, principal } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  if (isLoading) {
    return <p>Loading session…</p>
  }

  if (principal !== null) {
    return <Navigate to={principal.role === 'admin' ? '/admin' : '/tenant'} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    try {
      const authenticatedPrincipal = await login({ username, password })
      if (authenticatedPrincipal !== null) {
        navigate(authenticatedPrincipal.role === 'admin' ? '/admin' : '/tenant', { replace: true })
      }
    } catch {
      setError('Unable to sign in. Check your credentials and try again.')
    }
  }

  return (
    <main>
      <h1>Anti-DDoS Control Plane</h1>
      <form onSubmit={(event) => void handleSubmit(event)}>
        <label>
          Username
          <input
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
            required
            value={username}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
            required
            type="password"
            value={password}
          />
        </label>
        {error !== null ? <p role="alert">{error}</p> : null}
        <button type="submit">Sign in</button>
      </form>
    </main>
  )
}
