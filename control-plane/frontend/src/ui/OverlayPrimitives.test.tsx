import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Dialog, ConfirmDialog, Tabs, Toaster, toast } from './index'

afterEach(cleanup)

describe('Overlay Primitives', () => {
  describe('Dialog', () => {
    it('renders trigger and shows content when clicked', () => {
      render(
        <Dialog title="Edit Profile" trigger={<button>Open Dialog</button>}>
          <div>Dialog Content</div>
        </Dialog>
      )

      expect(screen.queryByText('Dialog Content')).not.toBeInTheDocument()
      
      fireEvent.click(screen.getByText('Open Dialog'))
      expect(screen.getByText('Dialog Content')).toBeInTheDocument()
      expect(screen.getByText('Edit Profile')).toBeInTheDocument()
    })

    it('triggers onOpenChange when close button is clicked', () => {
      const handleOpenChange = vi.fn()
      render(
        <Dialog open title="Edit Profile" onOpenChange={handleOpenChange}>
          <div>Dialog Content</div>
        </Dialog>
      )

      fireEvent.click(screen.getByLabelText('Close dialog'))
      expect(handleOpenChange).toHaveBeenCalledWith(false)
    })
  })

  describe('ConfirmDialog', () => {
    it('calls onConfirm and onOpenChange when confirmed', () => {
      const handleOpenChange = vi.fn()
      const handleConfirm = vi.fn()
      render(
        <ConfirmDialog
          open
          onOpenChange={handleOpenChange}
          title="Delete Item"
          description="Are you sure you want to delete this?"
          confirmLabel="Delete"
          onConfirm={handleConfirm}
          tone="danger"
        />
      )

      expect(screen.getByText('Delete Item')).toBeInTheDocument()
      expect(screen.getByText('Are you sure you want to delete this?')).toBeInTheDocument()
      
      fireEvent.click(screen.getByText('Delete'))
      expect(handleConfirm).toHaveBeenCalled()
      expect(handleOpenChange).toHaveBeenCalledWith(false)
    })

    it('closes on cancel', () => {
      const handleOpenChange = vi.fn()
      const handleConfirm = vi.fn()
      render(
        <ConfirmDialog
          open
          onOpenChange={handleOpenChange}
          title="Delete Item"
          description="Are you sure you want to delete this?"
          onConfirm={handleConfirm}
        />
      )

      fireEvent.click(screen.getByText('Cancel'))
      expect(handleConfirm).not.toHaveBeenCalled()
      expect(handleOpenChange).toHaveBeenCalledWith(false)
    })
  })

  describe('Tabs', () => {
    it('switches between tab content panels on click', () => {
      render(
        <Tabs.Root defaultValue="tab-1">
          <Tabs.List>
            <Tabs.Trigger value="tab-1">Tab 1</Tabs.Trigger>
            <Tabs.Trigger value="tab-2">Tab 2</Tabs.Trigger>
          </Tabs.List>
          <Tabs.Content value="tab-1">Panel 1</Tabs.Content>
          <Tabs.Content value="tab-2">Panel 2</Tabs.Content>
        </Tabs.Root>
      )

      expect(screen.getByText('Panel 1')).toBeInTheDocument()
      expect(screen.queryByText('Panel 2')).not.toBeInTheDocument()

      const tab2 = screen.getByText('Tab 2')
      fireEvent.mouseDown(tab2)
      fireEvent.click(tab2)
      expect(screen.queryByText('Panel 1')).not.toBeInTheDocument()
      expect(screen.getByText('Panel 2')).toBeInTheDocument()
    })
  })

  describe('Toast', () => {
    it('dispatches and mounts toast correctly', () => {
      vi.useFakeTimers()
      
      render(
        <>
          <Toaster />
          <button onClick={() => toast({ title: 'Success Toast', description: 'Item created', variant: 'success' })}>
            Trigger Toast
          </button>
        </>
      )

      expect(screen.queryByText('Success Toast')).not.toBeInTheDocument()

      fireEvent.click(screen.getByText('Trigger Toast'))
      expect(screen.getByText('Success Toast')).toBeInTheDocument()
      expect(screen.getByText('Item created')).toBeInTheDocument()

      vi.useRealTimers()
    })
  })
})
