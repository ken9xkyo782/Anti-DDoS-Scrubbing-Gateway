import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { Button, Input, Spinner } from '../ui'
import { ThemeToggle } from '../layout/ThemeToggle'
import styles from './LoginPage.module.css'

const PRODUCT_NAME = 'Anti-DDoS Control Plane'

function ShieldLogo({ size = 26 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

const FEATURES = [
  'Line-rate XDP packet scrubbing at the edge',
  'Real-time telemetry, alerting, and drop analytics',
  'Multi-tenant policy and allocation control',
]

export function LoginPage() {
  const { isLoading, login, principal } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const usernameId = useId()
  const passwordId = useId()
  const errorId = useId()

  if (isLoading) {
    return (
      <div className={styles.loading}>
        <Spinner size="lg" />
        <span>Loading session…</span>
      </div>
    )
  }

  if (principal !== null) {
    return <Navigate to={principal.role === 'admin' ? '/admin' : '/tenant'} replace />
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)

    try {
      const authenticatedPrincipal = await login({ username, password })
      if (authenticatedPrincipal !== null) {
        navigate(authenticatedPrincipal.role === 'admin' ? '/admin' : '/tenant', { replace: true })
      }
    } catch {
      setError('Unable to sign in. Check your credentials and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.page}>
      <aside className={styles.brandPanel}>
        <div className={styles.brandTop}>
          <span className={styles.logoMark}>
            <ShieldLogo size={26} />
          </span>
          <span className={styles.brandWordmark}>Scrubbing Gateway</span>
        </div>

        <div className={styles.brandBody}>
          <h1 className={styles.brandHeadline}>{PRODUCT_NAME}</h1>
          <p className={styles.brandSubhead}>
            Absorb volumetric attacks and keep protected services online with
            programmable, line-rate mitigation.
          </p>
        </div>

        <ul className={styles.featureList}>
          {FEATURES.map((feature) => (
            <li key={feature} className={styles.featureItem}>
              <span className={styles.featureIcon}>
                <CheckIcon />
              </span>
              <span>{feature}</span>
            </li>
          ))}
        </ul>
      </aside>

      <main className={styles.formPanel}>
        <div className={styles.themeToggle}>
          <ThemeToggle />
        </div>

        <div className={styles.card}>
          <div className={styles.mobileBrand}>
            <span className={styles.mobileLogo}>
              <ShieldLogo size={22} />
            </span>
            <span className={styles.mobileBrandName}>{PRODUCT_NAME}</span>
          </div>

          <header className={styles.header}>
            <h2 className={styles.title}>Welcome back</h2>
            <p className={styles.subtitle}>Sign in to access your control plane dashboard.</p>
          </header>

          <form className={styles.form} onSubmit={(event) => void handleSubmit(event)} noValidate>
            {error !== null ? (
              <div id={errorId} className={styles.alert} role="alert">
                <span className={styles.alertIcon} aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <path d="M12 8v4" />
                    <path d="M12 16h.01" />
                  </svg>
                </span>
                <span>{error}</span>
              </div>
            ) : null}

            <div className={styles.field}>
              <label htmlFor={usernameId} className={styles.label}>
                Username
              </label>
              <div className={styles.control}>
                <span className={styles.leadingIcon} aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
                    <circle cx="12" cy="7" r="4" />
                  </svg>
                </span>
                <Input
                  id={usernameId}
                  className={styles.input}
                  autoComplete="username"
                  autoFocus
                  disabled={submitting}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="Enter your username"
                  required
                  value={username}
                  aria-describedby={error !== null ? errorId : undefined}
                />
              </div>
            </div>

            <div className={styles.field}>
              <label htmlFor={passwordId} className={styles.label}>
                Password
              </label>
              <div className={styles.control}>
                <span className={styles.leadingIcon} aria-hidden="true">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect width="18" height="11" x="3" y="11" rx="2" ry="2" />
                    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                  </svg>
                </span>
                <Input
                  id={passwordId}
                  className={`${styles.input} ${styles.inputWithToggle}`}
                  autoComplete="current-password"
                  disabled={submitting}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Enter your password"
                  required
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  aria-describedby={error !== null ? errorId : undefined}
                />
                <button
                  type="button"
                  className={styles.toggleButton}
                  onClick={() => setShowPassword((visible) => !visible)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  aria-pressed={showPassword}
                  tabIndex={-1}
                >
                  {showPassword ? (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                      <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
                      <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
                      <line x1="2" x2="22" y1="2" y2="22" />
                    </svg>
                  ) : (
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
                      <circle cx="12" cy="12" r="3" />
                    </svg>
                  )}
                </button>
              </div>
            </div>

            <Button
              className={styles.submit}
              type="submit"
              size="lg"
              loading={submitting}
            >
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>

          <div className={styles.footer}>
            <ShieldLogo size={14} />
            <span>Secured connection · Authorized access only</span>
          </div>
        </div>
      </main>
    </div>
  )
}
