import React from 'react'
import { DropdownMenu as RadixDropdownMenu } from 'radix-ui'
import styles from './DropdownMenu.module.css'

export const DropdownMenuRoot = RadixDropdownMenu.Root
export const DropdownMenuTrigger = RadixDropdownMenu.Trigger
export const DropdownMenuPortal = RadixDropdownMenu.Portal

export const DropdownMenuContent = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixDropdownMenu.Content>
>(({ className = '', sideOffset = 4, ...props }, ref) => (
  <RadixDropdownMenu.Content
    ref={ref}
    sideOffset={sideOffset}
    className={`${styles.content} ${className}`}
    {...props}
  />
))
DropdownMenuContent.displayName = 'DropdownMenuContent'

export const DropdownMenuItem = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixDropdownMenu.Item>
>(({ className = '', ...props }, ref) => (
  <RadixDropdownMenu.Item
    ref={ref}
    className={`${styles.item} ${className}`}
    {...props}
  />
))
DropdownMenuItem.displayName = 'DropdownMenuItem'

export const DropdownMenuSeparator = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixDropdownMenu.Separator>
>(({ className = '', ...props }, ref) => (
  <RadixDropdownMenu.Separator
    ref={ref}
    className={`${styles.separator} ${className}`}
    {...props}
  />
))
DropdownMenuSeparator.displayName = 'DropdownMenuSeparator'

export const DropdownMenu = {
  Root: DropdownMenuRoot,
  Trigger: DropdownMenuTrigger,
  Portal: DropdownMenuPortal,
  Content: DropdownMenuContent,
  Item: DropdownMenuItem,
  Separator: DropdownMenuSeparator,
}
