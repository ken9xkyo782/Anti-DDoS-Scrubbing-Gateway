import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, apiClient, fieldErrorsFrom422 } from './client'

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

  it('parses detail string message for non-ok status', async () => {
    const errorBody = JSON.stringify({ detail: 'Invalid input data' })
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(errorBody, {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    }))

    const promise = apiClient('/services')
    await expect(promise).rejects.toBeInstanceOf(ApiError)
    
    try {
      await promise
    } catch (err) {
      const apiErr = err as ApiError
      expect(apiErr.status).toBe(400)
      expect(apiErr.message).toBe('Invalid input data')
      expect(apiErr.detail).toBe('Invalid input data')
    }
  })

  it('parses detail array structures on 422 validations', async () => {
    const validationDetails = [
      { loc: ['body', 'name'], msg: 'field required', type: 'value_error.missing' },
      { loc: ['body', 'ip_prefixes', 0], msg: 'invalid IP format', type: 'value_error.ip' }
    ]
    const errorBody = JSON.stringify({ detail: validationDetails })
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(errorBody, {
      status: 422,
      headers: { 'Content-Type': 'application/json' },
    }))

    const promise = apiClient('/services')
    await expect(promise).rejects.toBeInstanceOf(ApiError)

    try {
      await promise
    } catch (err) {
      const apiErr = err as ApiError
      expect(apiErr.status).toBe(422)
      expect(apiErr.detail).toEqual(validationDetails)
    }
  })

  it('handles empty/failed json parsing gracefully on errors', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response('Internal Server Error', {
      status: 500,
    }))

    const promise = apiClient('/services')
    await expect(promise).rejects.toBeInstanceOf(ApiError)

    try {
      await promise
    } catch (err) {
      const apiErr = err as ApiError
      expect(apiErr.status).toBe(500)
      expect(apiErr.message).toBe('Request failed with status 500')
      expect(apiErr.detail).toBeUndefined()
    }
  })
})

describe('fieldErrorsFrom422', () => {
  it('maps array validation details to field errors correctly', () => {
    const details = [
      { loc: ['body', 'name'], msg: 'field required', type: 'value_error.missing' },
      { loc: ['body', 'ip_prefixes', 2], msg: 'invalid IP', type: 'value_error.ip' },
      { loc: ['query', 'limit'], msg: 'must be positive', type: 'value_error' }
    ]

    const fieldErrors = fieldErrorsFrom422(details)
    expect(fieldErrors).toEqual({
      name: 'field required',
      ip_prefixes: 'invalid IP',
      limit: 'must be positive'
    })
  })

  it('returns empty object for non-array inputs', () => {
    expect(fieldErrorsFrom422(null)).toEqual({})
    expect(fieldErrorsFrom422('some string error')).toEqual({})
  })
})
