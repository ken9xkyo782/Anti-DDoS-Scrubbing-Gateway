import React from 'react'
import { Switch as RadixSwitch } from 'radix-ui'
import styles from './Switch.module.css'

export interface SwitchProps {
  id?: string
  checked?: boolean
  defaultChecked?: boolean
  onCheckedChange?: (checked: boolean) => void
  disabled?: boolean
  required?: boolean
  name?: string
  value?: string
  className?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}

export const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ id, checked, defaultChecked, onCheckedChange, disabled, required, name, value, className = '', 'aria-describedby': ariaDescribedBy, 'aria-invalid': ariaInvalid, ...props }, ref) => {
    return (
      <RadixSwitch.Root
        ref={ref}
        id={id}
        checked={checked}
        defaultChecked={defaultChecked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        required={required}
        name={name}
        value={value}
        className={`${styles.root} ${className}`}
        aria-describedby={ariaDescribedBy}
        aria-invalid={ariaInvalid ? 'true' : undefined}
        {...props}
      >
        <RadixSwitch.Thumb className={styles.thumb} />
      </RadixSwitch.Root>
    )
  }
)

Switch.displayName = 'Switch'
