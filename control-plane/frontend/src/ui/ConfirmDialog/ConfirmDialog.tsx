import React from 'react'
import { AlertDialog } from 'radix-ui'
import { Button } from '../Button/Button'
import styles from './ConfirmDialog.module.css'

export interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  tone?: 'info' | 'danger'
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({ open, onOpenChange, title, description, confirmLabel = 'Confirm', cancelLabel = 'Cancel', onConfirm, tone = 'info' }) => {
  return (
    <AlertDialog.Root open={open} onOpenChange={onOpenChange}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={styles.overlay} />
        <AlertDialog.Content className={styles.content}>
          <AlertDialog.Title className={styles.title}>{title}</AlertDialog.Title>
          <AlertDialog.Description className={styles.description}>
            {description}
          </AlertDialog.Description>
          <div className={styles.footer}>
            <AlertDialog.Cancel asChild>
              <Button variant="secondary">{cancelLabel}</Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action asChild>
              <Button
                variant={tone === 'danger' ? 'danger' : 'primary'}
                onClick={(e) => {
                  e.preventDefault()
                  onConfirm()
                  onOpenChange(false)
                }}
              >
                {confirmLabel}
              </Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  )
}
