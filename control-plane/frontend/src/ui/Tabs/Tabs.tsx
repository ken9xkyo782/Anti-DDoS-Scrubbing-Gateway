import React from 'react'
import { Tabs as RadixTabs } from 'radix-ui'
import styles from './Tabs.module.css'

export const TabsRoot = RadixTabs.Root

export const TabsList = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixTabs.List>
>(({ className = '', ...props }, ref) => (
  <RadixTabs.List
    ref={ref}
    className={`${styles.list} ${className}`}
    {...props}
  />
))
TabsList.displayName = 'TabsList'

export const TabsTrigger = React.forwardRef<
  HTMLButtonElement,
  React.ComponentPropsWithoutRef<typeof RadixTabs.Trigger>
>(({ className = '', ...props }, ref) => (
  <RadixTabs.Trigger
    ref={ref}
    className={`${styles.trigger} ${className}`}
    {...props}
  />
))
TabsTrigger.displayName = 'TabsTrigger'

export const TabsContent = React.forwardRef<
  HTMLDivElement,
  React.ComponentPropsWithoutRef<typeof RadixTabs.Content>
>(({ className = '', ...props }, ref) => (
  <RadixTabs.Content
    ref={ref}
    className={`${styles.content} ${className}`}
    {...props}
  />
))
TabsContent.displayName = 'TabsContent'

export const Tabs = {
  Root: TabsRoot,
  List: TabsList,
  Trigger: TabsTrigger,
  Content: TabsContent,
}
