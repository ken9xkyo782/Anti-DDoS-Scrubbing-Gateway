import React, { useId } from 'react'
import styles from './Field.module.css'

export interface FieldProps {
  label: string
  error?: string
  hint?: string
  required?: boolean
  children: React.ReactElement<{ id?: string; 'aria-describedby'?: string; 'aria-invalid'?: string }>
}

export const Field: React.FC<FieldProps> = ({ label, error, hint, required, children }) => {
  const baseId = useId()
  const controlId = children.props.id || `field-control-${baseId}`
  const hintId = `field-hint-${baseId}`
  const errorId = `field-error-${baseId}`

  const hasHint = !!hint
  const hasError = !!error

  const describedBy = [
    hasHint ? hintId : null,
    hasError ? errorId : null,
  ].filter(Boolean).join(' ') || undefined

  const clonedChild = React.cloneElement(children, {
    id: controlId,
    'aria-describedby': describedBy,
    'aria-invalid': hasError ? 'true' : undefined,
  })

  return (
    <div className={styles.fieldContainer}>
      <label htmlFor={controlId} className={styles.label}>
        {label}
        {required && <span className={styles.requiredAsterisk} aria-hidden="true"> *</span>}
      </label>
      <div className={styles.controlWrapper}>
        {clonedChild}
      </div>
      {hasError && (
        <div id={errorId} className={styles.error} role="alert">
          {error}
        </div>
      )}
      {hasHint && !hasError && (
        <div id={hintId} className={styles.hint}>
          {hint}
        </div>
      )}
    </div>
  )
}
