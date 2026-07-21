import React, { useEffect, useState } from 'react'
import { Toast as RadixToast } from 'radix-ui'
import styles from './Toast.module.css'

export type ToastVariant = 'success' | 'error' | 'info'

export interface ToastData {
  id: string
  title: string
  description?: string
  variant?: ToastVariant
  duration?: number
}

type Listener = (toasts: ToastData[]) => void
let listeners: Listener[] = []
let toasts: ToastData[] = []

const notify = () => {
  listeners.forEach((l) => l([...toasts]))
}

export const toast = ({ title, description, variant = 'info', duration = 5000 }: Omit<ToastData, 'id'>) => {
  const id = Math.random().toString(36).substring(2, 9)
  const newToast: ToastData = { id, title, description, variant, duration }
  toasts = [...toasts, newToast]
  notify()

  if (duration !== Infinity) {
    setTimeout(() => {
      dismissToast(id)
    }, duration)
  }
}

export const dismissToast = (id: string) => {
  toasts = toasts.filter((t) => t.id !== id)
  notify()
}

export function useToast() {
  const [activeToasts, setActiveToasts] = useState<ToastData[]>(toasts)

  useEffect(() => {
    const listener = (newToasts: ToastData[]) => {
      setActiveToasts(newToasts)
    }
    listeners.push(listener)
    return () => {
      listeners = listeners.filter((l) => l !== listener)
    }
  }, [])

  return {
    toasts: activeToasts,
    toast,
    dismiss: dismissToast,
  }
}

export const Toaster: React.FC = () => {
  const { toasts, dismiss } = useToast()

  return (
    <RadixToast.Provider swipeDirection="right">
      {toasts.map(({ id, title, description, variant = 'info' }) => (
        <RadixToast.Root
          key={id}
          className={`${styles.root} ${styles[variant]}`}
          onOpenChange={(open) => {
            if (!open) {
              dismiss(id)
            }
          }}
        >
          <div className={styles.content}>
            <RadixToast.Title className={styles.title}>{title}</RadixToast.Title>
            {description && (
              <RadixToast.Description className={styles.description}>
                {description}
              </RadixToast.Description>
            )}
          </div>
          <RadixToast.Close className={styles.closeButton} aria-label="Close toast">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </RadixToast.Close>
        </RadixToast.Root>
      ))}
      <RadixToast.Viewport className={styles.viewport} />
    </RadixToast.Provider>
  )
}
