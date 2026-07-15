import React from 'react'
import styles from './Textarea.module.css'

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className = '', ...props }, ref) => {
    return <textarea ref={ref} className={`${styles.textarea} ${className}`} {...props} />
  }
)

Textarea.displayName = 'Textarea'
