import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { apiClient } from '../api/client'

export type Role = 'admin' | 'tenant_user'

export interface Principal {
  id: string
  username: string
  role: Role
  tenant_id: string | null
}

export interface LoginCredentials {
  username: string
  password: string
}

export interface AuthContextValue {
  principal: Principal | null
  isLoading: boolean
  login: (credentials: LoginCredentials) => Promise<Principal | null>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [principal, setPrincipal] = useState<Principal | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const refreshPrincipal = useCallback(async () => {
    try {
      const currentPrincipal = await apiClient<Principal>('/auth/me')
      setPrincipal(currentPrincipal)
      return currentPrincipal
    } catch {
      setPrincipal(null)
      return null
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void Promise.resolve().then(refreshPrincipal)
  }, [refreshPrincipal])

  const value = useMemo<AuthContextValue>(
    () => ({
      principal,
      isLoading,
      async login(credentials) {
        await apiClient<Principal>('/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(credentials),
        })
        return refreshPrincipal()
      },
      async logout() {
        await apiClient<void>('/auth/logout', { method: 'POST' })
        setPrincipal(null)
      },
    }),
    [isLoading, principal, refreshPrincipal],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used inside an AuthProvider')
  }
  return context
}
