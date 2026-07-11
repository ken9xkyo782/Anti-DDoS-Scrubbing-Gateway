export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message = `Request failed with status ${status}`,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

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
    throw new ApiError(response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}
