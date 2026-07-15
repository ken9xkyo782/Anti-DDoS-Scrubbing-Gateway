import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Button, Field, Input, Select, Switch } from './index'

afterEach(cleanup)

describe('Form Primitives', () => {
  describe('Button', () => {
    it('renders text and children correctly', () => {
      render(<Button>Click me</Button>)
      expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument()
    })

    it('is disabled when disabled prop is true', () => {
      render(<Button disabled>Click me</Button>)
      expect(screen.getByRole('button')).toBeDisabled()
    })

    it('is disabled and shows spinner when loading is true', () => {
      render(<Button loading>Click me</Button>)
      const button = screen.getByRole('button')
      expect(button).toBeDisabled()
      expect(button.querySelector('svg')).toBeInTheDocument()
    })
  })

  describe('Field', () => {
    it('wires the label to the control using htmlFor and unique IDs', () => {
      render(
        <Field label="Username">
          <Input placeholder="Enter username" />
        </Field>
      )
      
      const label = screen.getByText('Username')
      const input = screen.getByPlaceholderText('Enter username')
      
      expect(label).toBeInTheDocument()
      expect(input).toBeInTheDocument()
      expect(label.getAttribute('for')).toBe(input.id)
    })

    it('wires hint text to the control via aria-describedby', () => {
      render(
        <Field label="Password" hint="Must be at least 8 characters">
          <Input type="password" />
        </Field>
      )
      
      const input = screen.getByLabelText('Password')
      const hint = screen.getByText('Must be at least 8 characters')
      
      expect(input.getAttribute('aria-describedby')).toBe(hint.id)
      expect(input.getAttribute('aria-invalid')).toBeNull()
    })

    it('wires error message to the control via aria-describedby and sets aria-invalid', () => {
      render(
        <Field label="Email" error="Invalid email address">
          <Input type="email" />
        </Field>
      )
      
      const input = screen.getByLabelText('Email')
      const error = screen.getByText('Invalid email address')
      
      expect(input.getAttribute('aria-describedby')).toBe(error.id)
      expect(input.getAttribute('aria-invalid')).toBe('true')
    })
  })

  describe('Select', () => {
    const options = [
      { value: 'option-1', label: 'Option 1' },
      { value: 'option-2', label: 'Option 2', disabled: true },
    ]

    it('renders placeholder correctly', () => {
      render(<Select options={options} placeholder="Choose options..." />)
      expect(screen.getByText('Choose options...')).toBeInTheDocument()
    })

    it('disables the select trigger when disabled is true', () => {
      render(<Select options={options} disabled />)
      expect(screen.getByRole('combobox')).toBeDisabled()
    })
  })

  describe('Switch', () => {
    it('renders switch toggle correctly and supports clicking', () => {
      const handleChange = vi.fn()
      render(<Switch onCheckedChange={handleChange} />)
      
      const toggle = screen.getByRole('switch')
      expect(toggle).toBeInTheDocument()
      expect(toggle.getAttribute('aria-checked')).toBe('false')

      fireEvent.click(toggle)
      expect(handleChange).toHaveBeenCalledWith(true)
    })

    it('disables switch toggle when disabled is true', () => {
      render(<Switch disabled />)
      expect(screen.getByRole('switch')).toBeDisabled()
    })
  })
})
