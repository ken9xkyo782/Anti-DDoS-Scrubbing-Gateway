import React from 'react'
import styles from './NumberInput.module.css'

export type NumberInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'>

export const NumberInput = React.forwardRef<HTMLInputElement, NumberInputProps>(
  ({ className = '', ...props }, ref) => {
    return <input ref={ref} type="number" className={`${styles.numberInput} ${className}`} {...props} />
  }
)

NumberInput.displayName = 'NumberInput'
