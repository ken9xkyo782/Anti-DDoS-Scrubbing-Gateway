import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiClient } from './client'

describe('apiClient', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    window.history.replaceState(null, '', '/services')
  })

  it('includes cookies and redirects a 401 response to login', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValue(new Response(null, { status: 401 }))

    await expect(apiClient('/services')).rejects.toBeInstanceOf(ApiError)

    expect(fetchMock).toHaveBeenCalledWith('/services', expect.objectContaining({ credentials: 'include' }))
    expect(window.location.pathname).toBe('/login')
  })
})
