import React from 'react'
import { Select as RadixSelect } from 'radix-ui'
import styles from './Select.module.css'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps {
  id?: string
  options: SelectOption[]
  value?: string
  defaultValue?: string
  onValueChange?: (value: string) => void
  placeholder?: string
  disabled?: boolean
  name?: string
  required?: boolean
  className?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}

export const Select = React.forwardRef<HTMLButtonElement, SelectProps>(
  (
    {
      id,
      options,
      value,
      defaultValue,
      onValueChange,
      placeholder = 'Select an option…',
      disabled,
      name,
      required,
      className = '',
      'aria-describedby': ariaDescribedBy,
      'aria-invalid': ariaInvalid,
      ...props
    },
    ref
  ) => {
    const EMPTY_VALUE_PLACEHOLDER = '__empty_value__'

    const translatedValue = value === '' ? EMPTY_VALUE_PLACEHOLDER : value
    const translatedDefaultValue = defaultValue === '' ? EMPTY_VALUE_PLACEHOLDER : defaultValue

    const handleValueChange = onValueChange
      ? (val: string) => {
          onValueChange(val === EMPTY_VALUE_PLACEHOLDER ? '' : val)
        }
      : undefined

    return (
      <RadixSelect.Root
        value={translatedValue}
        defaultValue={translatedDefaultValue}
        onValueChange={handleValueChange}
        disabled={disabled}
        required={required}
        name={name}
      >
        <RadixSelect.Trigger
          ref={ref}
          id={id}
          className={`${styles.trigger} ${className}`}
          aria-describedby={ariaDescribedBy}
          aria-invalid={ariaInvalid ? 'true' : undefined}
          {...props}
        >
          <RadixSelect.Value placeholder={placeholder} />
          <RadixSelect.Icon className={styles.icon}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </RadixSelect.Icon>
        </RadixSelect.Trigger>

        <RadixSelect.Portal>
          <RadixSelect.Content className={styles.content} position="popper" sideOffset={4}>
            <RadixSelect.ScrollUpButton className={styles.scrollButton}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15" />
              </svg>
            </RadixSelect.ScrollUpButton>
            <RadixSelect.Viewport className={styles.viewport}>
              {options.map((option) => {
                const itemValue = option.value === '' ? EMPTY_VALUE_PLACEHOLDER : option.value
                return (
                  <RadixSelect.Item
                    key={itemValue}
                    value={itemValue}
                    disabled={option.disabled}
                    className={styles.item}
                  >
                    <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
                    <RadixSelect.ItemIndicator className={styles.itemIndicator}>
                      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    </RadixSelect.ItemIndicator>
                  </RadixSelect.Item>
                )
              })}
            </RadixSelect.Viewport>
            <RadixSelect.ScrollDownButton className={styles.scrollButton}>
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </RadixSelect.ScrollDownButton>
          </RadixSelect.Content>
        </RadixSelect.Portal>
      </RadixSelect.Root>
    )
  }
)

Select.displayName = 'Select'
