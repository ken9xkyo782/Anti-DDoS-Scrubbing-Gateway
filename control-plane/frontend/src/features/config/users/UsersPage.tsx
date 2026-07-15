import { useState } from 'react'
import {
  DataTable,
  PageHeader,
  Button,
  Dialog,
  EmptyState,
  ConfirmDialog,
  Badge,
} from '../../../ui'
import { toast } from '../../../ui/Toast/Toast'
import {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useResetPassword,
} from '../../../hooks/resources/useUsers'
import { UserForm } from './UserForm'
import { ResetPasswordDialog } from './ResetPasswordDialog'
import type { UserResponse, Role, UserStatus } from '../../../api/types'

export function UsersPage() {
  const { data: users = [], isLoading, error } = useUsers()
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<UserResponse | null>(null)
  const [resettingUser, setResettingUser] = useState<UserResponse | null>(null)
  const [deletingUser, setDeletingUser] = useState<UserResponse | null>(null)

  const createMutation = useCreateUser()
  const updateMutation = useUpdateUser(editingUser?.id ?? '')
  const deleteMutation = useDeleteUser(deletingUser?.id ?? '')
  const resetPasswordMutation = useResetPassword(resettingUser?.id ?? '')

  const handleCreateSubmit = async (payload: {
    username: string
    password?: string
    role: Role
    tenant_id?: string | null
  }) => {
    // password must exist on create
    await createMutation.mutateAsync({
      username: payload.username,
      password: payload.password ?? '',
      role: payload.role,
      tenant_id: payload.tenant_id,
    })
    toast({ title: 'User created successfully', variant: 'success' })
    setIsCreateOpen(false)
  }

  const handleEditSubmit = async (payload: {
    username: string
    role: Role
    tenant_id?: string | null
  }) => {
    if (!editingUser) return
    await updateMutation.mutateAsync(payload)
    toast({ title: 'User updated successfully', variant: 'success' })
    setEditingUser(null)
  }

  const handleDelete = async () => {
    if (!deletingUser) return
    try {
      await deleteMutation.mutateAsync()
      toast({ title: 'User deleted successfully', variant: 'success' })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({ title: 'Failed to delete user', description: message, variant: 'error' })
    } finally {
      setDeletingUser(null)
    }
  }

  const handleResetPasswordSubmit = async (newPassword: string) => {
    if (!resettingUser) return
    await resetPasswordMutation.mutateAsync({ new_password: newPassword })
    toast({ title: 'Password reset successfully', variant: 'success' })
    setResettingUser(null)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-6)', padding: 'var(--space-6)' }}>
      <PageHeader
        title="Users Management"
        description="Onboard users, manage credentials, assign roles, and handle tenant ownership."
        actions={
          users.length > 0 && (
            <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
              Create User
            </Button>
          )
        }
      />

      <DataTable<UserResponse>
        columns={[
          {
            key: 'username',
            header: 'Username',
            render: (user) => <span style={{ fontWeight: 600 }}>{user.username}</span>,
          },
          {
            key: 'role',
            header: 'Role',
            render: (user) => (
              <Badge variant={user.role === 'admin' ? 'info' : 'default'}>
                {user.role === 'admin' ? 'Admin' : 'Tenant User'}
              </Badge>
            ),
          },
          {
            key: 'tenant_name',
            header: 'Tenant Name',
            render: (user) => <span>{user.tenant_name ?? '—'}</span>,
          },
          {
            key: 'status',
            header: 'Status',
            render: (user) => (
              <Badge variant={user.status === 'active' ? 'success' : 'danger'}>
                {user.status === 'active' ? 'Active' : 'Disabled'}
              </Badge>
            ),
          },
          {
            key: 'last_login_at',
            header: 'Last Login',
            render: (user) => (
              <span>
                {user.last_login_at ? new Date(user.last_login_at).toLocaleString() : 'Never'}
              </span>
            ),
          },
        ]}
        data={users}
        isLoading={isLoading}
        error={error?.message}
        emptyState={
          <EmptyState
            title="No users found"
            description="Get started by creating your first user."
            action={
              <Button variant="primary" onClick={() => setIsCreateOpen(true)}>
                Create User
              </Button>
            }
          />
        }
        rowActions={(user) => (
          <UserRowActions
            user={user}
            onEdit={setEditingUser}
            onResetPassword={setResettingUser}
            onDelete={setDeletingUser}
          />
        )}
      />

      {/* Create Dialog */}
      <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen} title="Create User">
        <UserForm
          onSubmit={handleCreateSubmit}
          onCancel={() => setIsCreateOpen(false)}
          isSubmitting={createMutation.isPending}
        />
      </Dialog>

      {/* Edit Dialog */}
      <Dialog
        open={editingUser !== null}
        onOpenChange={(open) => {
          if (!open) setEditingUser(null)
        }}
        title="Edit User"
      >
        {editingUser && (
          <UserForm
            user={editingUser}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditingUser(null)}
            isSubmitting={updateMutation.isPending}
          />
        )}
      </Dialog>

      {/* Reset Password Dialog */}
      <ResetPasswordDialog
        user={resettingUser}
        onClose={() => setResettingUser(null)}
        onSubmit={handleResetPasswordSubmit}
        isSubmitting={resetPasswordMutation.isPending}
      />

      {/* Delete Confirmation */}
      <ConfirmDialog
        open={deletingUser !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingUser(null)
        }}
        title="Delete User"
        description={`Are you sure you want to delete user "${deletingUser?.username}"? This action cannot be undone.`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
      />
    </div>
  )
}

interface UserRowActionsProps {
  user: UserResponse
  onEdit: (user: UserResponse) => void
  onResetPassword: (user: UserResponse) => void
  onDelete: (user: UserResponse) => void
}

function UserRowActions({ user, onEdit, onResetPassword, onDelete }: UserRowActionsProps) {
  const updateMutation = useUpdateUser(user.id)

  const handleToggleStatus = async () => {
    try {
      const nextStatus: UserStatus = user.status === 'active' ? 'disabled' : 'active'
      await updateMutation.mutateAsync({ status: nextStatus })
      toast({
        title: `User ${nextStatus === 'active' ? 'enabled' : 'disabled'} successfully`,
        variant: 'success',
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An unknown error occurred'
      toast({
        title: `Failed to update user status`,
        description: message,
        variant: 'error',
      })
    }
  }

  const isMutating = updateMutation.isPending

  return (
    <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
      <Button variant="secondary" size="sm" onClick={() => onEdit(user)} disabled={isMutating}>
        Edit
      </Button>
      <Button variant="secondary" size="sm" onClick={handleToggleStatus} loading={isMutating}>
        {user.status === 'active' ? 'Disable' : 'Enable'}
      </Button>
      <Button variant="secondary" size="sm" onClick={() => onResetPassword(user)} disabled={isMutating}>
        Reset Pwd
      </Button>
      <Button variant="danger" size="sm" onClick={() => onDelete(user)} disabled={isMutating}>
        Delete
      </Button>
    </div>
  )
}
