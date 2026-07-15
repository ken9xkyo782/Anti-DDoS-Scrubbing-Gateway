import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import type { UserResponse, Role, UserStatus } from '../../api/types'

export function useUsers() {
  return useQuery<UserResponse[]>({
    queryKey: ['users'],
    queryFn: () => apiClient<UserResponse[]>('/users'),
  })
}

export function useUser(id: string | null) {
  return useQuery<UserResponse>({
    queryKey: ['users', id],
    queryFn: () => apiClient<UserResponse>(`/users/${id}`),
    enabled: id !== null,
  })
}

export function useCreateUser() {
  const queryClient = useQueryClient()

  return useMutation<
    UserResponse,
    Error,
    {
      username: string
      password: string
      role: Role
      tenant_id?: string | null
    }
  >({
    mutationFn: (payload) =>
      apiClient<UserResponse>('/users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
    },
  })
}

export function useUpdateUser(id: string) {
  const queryClient = useQueryClient()

  return useMutation<
    UserResponse,
    Error,
    {
      username?: string
      role?: Role
      tenant_id?: string | null
      status?: UserStatus
    }
  >({
    mutationFn: (payload) =>
      apiClient<UserResponse>(`/users/${id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      queryClient.invalidateQueries({ queryKey: ['users', id] })
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
    },
  })
}

export function useDeleteUser(id: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, void>({
    mutationFn: () =>
      apiClient<void>(`/users/${id}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      queryClient.invalidateQueries({ queryKey: ['tenants'] })
    },
  })
}

export function useResetPassword(id: string) {
  const queryClient = useQueryClient()

  return useMutation<void, Error, { new_password: string }>({
    mutationFn: (payload) =>
      apiClient<void>(`/users/${id}/reset-password`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users', id] })
    },
  })
}
