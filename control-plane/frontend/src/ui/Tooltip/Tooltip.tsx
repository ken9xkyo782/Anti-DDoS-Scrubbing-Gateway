import React from 'react'
import { Tooltip as RadixTooltip } from 'radix-ui'
import styles from './Tooltip.module.css'

export interface TooltipProps {
  content: React.ReactNode
  children: React.ReactNode
  delayDuration?: number
}

export const Tooltip: React.FC<TooltipProps> = ({ content, children, delayDuration = 400 }) => {
  return (
    <RadixTooltip.Provider>
      <RadixTooltip.Root delayDuration={delayDuration}>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content className={styles.content} sideOffset={4}>
            {content}
            <RadixTooltip.Arrow className={styles.arrow} />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  )
}
