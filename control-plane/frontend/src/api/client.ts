import { ApiError } from './errors'
import type { ValidationErrorDetail } from './errors'

export { ApiError, fieldErrorsFrom422 } from './errors'
export type { ValidationErrorDetail } from './errors'

function redirectToLogin() {
  if (window.location.pathname === '/login') {
    return
  }

  window.history.replaceState(null, '', '/login')
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export async function apiClient<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
  })

  if (response.status === 401) {
    redirectToLogin()
    throw new ApiError(response.status, 'Your session has expired')
  }

  if (!response.ok) {
    let detail: unknown = undefined
    let message = `Request failed with status ${response.status}`
    try {
      const body = await response.json()
      if (body && typeof body === 'object') {
        if ('detail' in body) {
          detail = body.detail
          if (typeof detail === 'string') {
            message = detail
          }
        }
      }
    } catch {
      // Ignore parsing errors, keep default message
    }
    throw new ApiError(response.status, message, detail as string | ValidationErrorDetail[] | undefined)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}
