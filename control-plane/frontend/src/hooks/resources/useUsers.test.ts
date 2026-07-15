import { describe, expect, it, vi, beforeEach } from 'vitest'

const { useQuery, useMutation, useQueryClient } = vi.hoisted(() => ({
  useQuery: vi.fn(),
  useMutation: vi.fn(),
  useQueryClient: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQuery(...args),
  useMutation: (...args: unknown[]) => useMutation(...args),
  useQueryClient: (...args: unknown[]) => useQueryClient(...args),
}))

const { apiClient } = vi.hoisted(() => ({
  apiClient: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiClient,
}))

import {
  useUsers,
  useUser,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useResetPassword,
} from './useUsers'

describe('useUsers hook family', () => {
  const mockInvalidateQueries = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useQueryClient.mockReturnValue({
      invalidateQueries: mockInvalidateQueries,
    })
  })

  describe('useUsers', () => {
    it('queries users list correctly', () => {
      useQuery.mockReturnValue({})
      useUsers()
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['users'],
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/users')
    })
  })

  describe('useUser', () => {
    it('queries user details correctly when id is provided', () => {
      useQuery.mockReturnValue({})
      useUser('user-1')
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['users', 'user-1'],
          enabled: true,
        })
      )

      const queryFn = vi.mocked(useQuery).mock.calls[0][0].queryFn
      queryFn()
      expect(apiClient).toHaveBeenCalledWith('/users/user-1')
    })

    it('disables query when id is null', () => {
      useQuery.mockReturnValue({})
      useUser(null)
      expect(useQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['users', null],
          enabled: false,
        })
      )
    })
  })

  describe('useCreateUser', () => {
    it('submits create user payload and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useCreateUser()
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { username: 'john', password: 'pwd', role: 'tenant_user' as const, tenant_id: 'tenant-1' }
      
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['users'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
    })
  })

  describe('useUpdateUser', () => {
    it('submits patch payload and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useUpdateUser('user-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { role: 'admin' as const }

      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/users/user-1', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['users'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['users', 'user-1'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
    })
  })

  describe('useDeleteUser', () => {
    it('calls delete user endpoint and invalidates caches', async () => {
      useMutation.mockReturnValue({})
      useDeleteUser('user-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      await mutationOpts.mutationFn()
      expect(apiClient).toHaveBeenCalledWith('/users/user-1', {
        method: 'DELETE',
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['users'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['tenants'] })
    })
  })

  describe('useResetPassword', () => {
    it('calls reset password endpoint and invalidates cache', async () => {
      useMutation.mockReturnValue({})
      useResetPassword('user-1')
      expect(useMutation).toHaveBeenCalled()

      const mutationOpts = vi.mocked(useMutation).mock.calls[0][0]
      const payload = { new_password: 'new-pwd' }
      await mutationOpts.mutationFn(payload)
      expect(apiClient).toHaveBeenCalledWith('/users/user-1/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      mutationOpts.onSuccess()
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['users', 'user-1'] })
    })
  })
})
